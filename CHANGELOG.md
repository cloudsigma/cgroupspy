# Changelog

## 0.3.0 - 2026-10-29

### Added

* An explicit, read-only-by-default `cgroupspy.v2` API with native CPU, memory,
  cpuset, task-limit and I/O controller views; lifecycle, membership, capability
  and PSI reads; and explicit delegated mutation methods.
* Namespace-aware v1/v2 mount discovery, including co-mounted controllers,
  custom mount names, escaped mountinfo paths, and bind-mounted roots.
* Multiple logical controller views for co-mounted v1 directories, preserving
  existing primary `.controller` and `.controller_type` behavior on exact mounts.
* Optional v1 `create_missing`, `read_only`, and `controller_map` constructor
  arguments; explicit snapshot refresh; grouped raw-path and plural lookup.
* Modern libvirt `scope/libvirt/{emulator,vcpu*}` fallback while retaining the VM
  scope aggregate and legacy direct-child lookup.
* `Controller.get_content(..., strict=True)` to propagate filesystem errors and
  `CpuSetController.memory_pressure_rate` to expose the numeric measurement
  alongside the historical boolean property.
* Separate v1 and v2 batch-write errors retaining progress, failed record, and
  original kernel error. Earlier successful commands remain applied.

### Fixed

* Multiple requested v1 subgroups now remain siblings; overlapping prefixes
  reuse nodes instead of creating unintended nested paths or duplicate nodes.
* Root lookups, bytes-name deletion and VM lookup, stale-node cleanup, and
  traversal during expected descendant disappearance.
* Safe immediate v1 controller aliases remain visible in `Tree`; grouped views
  deduplicate the same physical controller while keeping all logical views.
* Existing regular files are rejected as successful cgroup creation targets.
* Device permission bits, device/throttle input validation, blkio aggregate
  parsing, descriptor discovery, and multi-record command writes.
* Trailing spaces in both block-I/O write-throttle filenames.
* v1 OOM control writes serialize only the writable `oom_kill_disable` flag.

### Compatibility and tooling

* Retained Python 2.7/Python 3.6+ metadata, v1 byte paths, `IntegerFile`'s
  `None`/`-1` mapping, historical grouped paths, and default subgroup creation.
* Unsafe child names, traversal, escaping/nested symlinks, malformed device
  permissions and invalid CPU ranges are intentionally rejected. See the
  [compatibility notes](docs/compatibility.md).
* Added PEP 517 build configuration with legacy build requirements, universal
  wheel configuration, distribution metadata, source-distribution tests/docs,
  and interpreter-specific test dependencies.
* Added GitHub and refreshed GitLab CI matrices, distribution checks, and
  opt-in Linux kernel integration coverage. Removed the stale Travis config.
  Private snapshot triggers retain the branch variable, add the exact commit,
  and fail on HTTP errors; trigger acceptance does not certify downstream success.
* Kept nose2's original generated cases and added regression fixtures. Local
  verification does not replace the remote interpreter matrix, Linux kernel
  integration, or a Turlo consumer test. See [testing](docs/testing.md).

## 0.2.3 - 2024-03-19

* Tolerated an external creator winning the cgroup `mkdir` race by handling
  `EEXIST` ([upstream PR #11](https://github.com/cloudsigma/cgroupspy/pull/11)).
* Bumped the package version to 0.2.3.

Earlier history is available in the repository's Git tags and commit log.
