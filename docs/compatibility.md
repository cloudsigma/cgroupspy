# Compatibility contract

0.3.0 extends the existing v1 API and adds a separate native v2 backend. The
package metadata continues to accept Python 2.7 and Python 3.6 or newer, with
Python 3.0-3.5 excluded. Retaining old interpreter compatibility does not change
those interpreters' upstream support status. Real cgroup access requires Linux.

## Preserved v1 behavior

* Existing imports, controller properties, tree classes, default root paths and
  byte-valued node paths remain available.
* Missing requested subgroups are still created by default. Monitoring callers
  can opt into `read_only=True` or `create_missing=False`.
* `IntegerFile` still converts a parsed `-1` to `None` and writes `None` as `-1`.
  Kernel files exposing a large integer for an unlimited value retain that
  integer; the library does not invent a universal v1 unlimited sentinel.
* Existing grouped paths still omit scope/slice/partition suffixes and existing
  first-match lookup remains. New raw/plural lookup handles collisions.
* Safe immediate controller aliases remain visible, while combined mounts add
  logical controller views. See [discovery and v1](discovery-and-v1.md).
* `get_content(key)` retains its historical `None` result for selected missing,
  denied and invalid-interface errors. `get_content(key, strict=True)` propagates
  the underlying error instead.
* `CpuSetController.memory_pressure` retains its boolean behavior. The new
  read-only `memory_pressure_rate` returns the numeric kernel measurement.

## Native v2 values are distinct

| Concept | Existing v1 API | New v2 API |
| --- | --- | --- |
| Paths | Bytes | Text; accepts bytes/text input |
| CPU distribution | `cpu.shares` | `cpu.weight`; no automatic conversion |
| CPU accounting | `cpuacct.usage` in nanoseconds | `cpu.stat['usage_usec']` in microseconds |
| Memory plus swap | `memory.memsw_*` covers combined accounting/limits | `memory.swap_*` is swap-only |
| Unlimited value | Existing per-interface `-1`/`None` behavior | Kernel `max` parses as `None` |
| Membership | Existing `tasks` and `procs` properties | Explicit process/thread attachment methods and sets |
| Tree lifecycle | Cached snapshots plus explicit `refresh()` | Live traversal and explicit descriptor close |

Do not substitute v2 values into existing Turlo/v1 calculations without checking
units, hierarchy and controller meaning. See the [v2 API](cgroup-v2.md) for
capabilities and host-dependent file availability.

## Corrected command writes and partial failures

Multi-record membership, device and mapping commands now validate the complete
input and send records independently. This corrects the old behavior in which
kernel command files could accept only part of an apparent multi-record write.
Empty collections do not generate spurious commands. There is no transaction
or rollback when a later kernel command fails.

The exception classes are deliberately separate:

```python
from cgroupspy.interfaces import BatchWriteError as V1BatchWriteError
from cgroupspy.v2 import BatchWriteError as V2BatchWriteError
```

Both derive from `IOError` and expose `failed_record`, `original_error`, `errno`
and `filename`. V1 exposes `completed_records` and `record_index`; v2 exposes
`completed`. Progress is a tuple of accepted record strings, not a guarantee
that the full requested batch succeeded. Effects of the failed command can be
indeterminate after an I/O or short-write error. Inspect current state before
retrying or compensating.

## Intentional input hardening

Malformed inputs that previously escaped a selected parent, silently produced
incorrect values, or depended on permissive parsing now raise an error:

* Child-name operations reject empty names, absolute paths, separators,
  traversal components and NUL bytes. Subgroup paths reject `.` and `..`.
* Existing regular files are not accepted as created cgroups. Nested or escaping
  symlinks are not accepted as subgroup mutation targets. V2 additionally uses
  descriptor-anchored traversal; v1 remains path-based.
* Invalid device types, permission letters/masks, negative device identifiers,
  fractional identifiers and invalid throttle values are rejected. Zero
  permission masks remain inspectable but cannot be serialized as commands;
  wildcard device identifiers remain representable. All-device commands with
  misleading narrower rights or device numbers are rejected.
* Negative or descending CPU/node ranges and non-integer mask members are
  rejected. Existing valid integer-set serialization and empty v1 masks remain.
* `memory.oom_control` reads a status dictionary, but only `oom_kill_disable`
  is writable. Assign a boolean/integer or a dictionary containing exactly that
  key; assigning the full read-only status snapshot now raises `ValueError`.

These are corrective hardening changes. Consumers relying on invalid or unsafe
inputs should correct their requests rather than suppressing the new errors.

## Consumer validation

Turlo's private GitLab source and deployment were unreachable during this work.
Turlo compatibility is therefore not certified. Before rollout, test its pinned
dependency/install route, Python versions, imports, VM naming, returned types,
units, exception handling, multi-record setters and actual cgroup mount layout.
Exercise the deployment's libvirt/systemd ownership arrangement and a rollback
to its previous cgroupspy version. A passing unit suite alone cannot establish
that consumer contract.
