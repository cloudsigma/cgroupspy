# Testing and release verification

## Unit and fixture tests

The suite includes the original nose2 generated cases and new codecs,
node/tree, discovery and v2 regressions. Use nose2 so
generator cases continue to execute; changing the runner alone to modern
pytest would lose compatibility with those tests.

```sh
python -m pip install -r requirements/test.txt
python -m pip install .
python -m nose2 -v
```

`requirements/test.txt` selects test dependencies for legacy and modern
interpreters. `tox.ini` configures Python 2.7 and 3.6-3.15; each interpreter must
exist where its environment runs. GitHub and GitLab CI configure separate
interpreter jobs. Package metadata retains the Python 2.7/3.6+ contract.
Legacy source/build compatibility must be tested with that interpreter's
supported tooling, not inferred from a modern Python test result.

Optional coverage on a compatible modern interpreter:

```sh
python -m pip install coverage
python -m coverage run --source=cgroupspy --omit='cgroupspy/test/*' -m nose2
python -m coverage report -m
```

Regular-directory fixtures validate parsing, serialization, paths, snapshots,
descriptor anchoring, and fault handling. They do not emulate cgroupfs command
accumulation, delegation, process migration, topology or resource enforcement.

Check the actual workflow results for the commit being released, including all
configured interpreter and Linux integration jobs. A local fixture result does
not establish kernel enforcement or cross-interpreter compatibility.

## Distribution checks

Build and inspect both distributions with a modern build interpreter:

```sh
python -m pip install build twine
python -m build
python -m twine check dist/*
```

The source archive includes tests, documentation and test requirements. The
runtime wheel omits the test package. Verify a wheel import outside the checkout
so local sources cannot hide packaging errors; then extract the source archive
into a clean directory, install its test requirements, and run nose2 there.
The distribution CI job performs these checks. Inspect the universal wheel tag
and exercise installation on retained legacy runtimes as well as current ones.
Build-system requirements have separate Python 2 constraints; avoid raising
them globally when refreshing packaging tools.

## Opt-in Linux kernel integration

`tests/integration_linux.py` uses Python 3-only test tooling and Linux kernel
facilities. It is an opt-in integration entry point, not part of the Python 2
runtime API or ordinary nose2 suite. The workflow's `linux-integration` job runs
it in disposable CI-owned hierarchies. Use a disposable Linux machine for the
privileged recipes below; the script refuses to run without explicit opt-in.

The v2 recipe requires a service manager that grants the requested controllers:

```sh
sudo systemd-run --wait --pipe --collect \
  --unit=cgroupspy-integration-manual \
  --property=Delegate=yes \
  env PYTHONPATH="$PWD" CGROUPSPY_RUN_LINUX_INTEGRATION=1 \
  "$(command -v python3)" "$PWD/tests/integration_linux.py"
```

The script verifies its service-root identity, moves only its own interpreter
and child workloads, explicitly enables required controllers, and checks native
limits, membership, CPU throttling, effective masks, freeze events, partial
kernel-command failure and cleanup. Required controllers must be available;
some optional file checks depend on kernel features. The I/O test needs a real
block device when the I/O controller is available.

The v1 command-record test uses a private mount namespace:

```sh
sudo unshare --mount --propagation private \
  env PYTHONPATH="$PWD" CGROUPSPY_RUN_LINUX_INTEGRATION=1 \
  "$(command -v python3)" "$PWD/tests/integration_linux.py" --v1
```

It mounts a temporary, controller-free named v1 hierarchy and validates
multi-record `cgroup.procs` and `tasks` writes using only its child processes.
It does not detach resource controllers from v2. A private mount namespace
isolates mount visibility, **not the named v1 hierarchy's kernel-global
identity**. The hierarchy is removed after cleanup and unmounting. This test
does not certify every v1 resource controller or a production VM host.

## Release evidence and consumers

Record the exact commit, interpreter matrix results, distribution checks and
Linux integration results in the release review. The private GitLab snapshots
trigger preserves its branch contract and adds `CGROUPSPY_REVISION`; a successful
HTTP trigger response does not prove that downstream build completed.

Turlo was unreachable during this work and remains an unverified consumer.
Run its deployment-specific compatibility tests before rollout, as described
in [compatibility](compatibility.md). No documentation statement substitutes
for a passing CI run or a consumer integration result.
