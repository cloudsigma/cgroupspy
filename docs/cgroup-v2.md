# Native cgroup v2 API

`cgroupspy.v2` is additive. It does not reinterpret the existing v1 controller
properties or select a host hierarchy implicitly. Pass a namespace-visible
v2 mount or subtree containing `cgroup.controllers`.

```python
from cgroupspy.v2 import V2Tree

with V2Tree('/sys/fs/cgroup') as tree:
    print(tree.root.capabilities)
    node = tree.get_node_by_path('/my.slice/example.scope')
    if node is not None and node.has_file('memory.current'):
        print(node.memory.current)
```

Tree construction and reads do not change controller enablement, process
membership, group layout, or freeze state. Close a tree explicitly or use its
context manager. `get_node_by_path` returns a node or `None` for a missing group.
Paths accept text/bytes input; new v2 names, paths and full paths are text.
Unlike cached v1 trees, `children` and `walk()` read a live, non-atomic hierarchy.

## Ownership and writes

Writes require both flags and an existing delegated root selected by the caller:

```python
from cgroupspy.v2 import CpuMax, V2Tree

with V2Tree('/sys/fs/cgroup/my-delegated.service',
            writable=True, delegated=True) as tree:
    # The caller arranges ownership, topology and required enabled controllers.
    workload = tree.root.create_cgroup('workload')
    workload.cpu.max = CpuMax(20000, 100000)
    workload.memory.max = 128 * 1024 * 1024
    workload.pids.max = 32
```

`delegated=True` asserts that the caller owns the subtree. It does not establish
delegation, verify service-manager ownership, bypass permissions, or satisfy
kernel topology rules. Controller enablement and process migration are separate
explicit operations; the library never performs them to make a setter succeed.
Use service-manager delegation for managed hosts and coordinate ownership with
libvirt/systemd rather than competing with them for the same groups.

V2 operations are anchored to a root directory descriptor. Descendant traversal
refuses symlinks and `.`/`..` components; command files must already exist.
Python 3 uses directory-relative operations where available; retained Python 2
uses Linux `/proc/self/fd` anchoring. The deliberately selected root path may
itself resolve through symlinks. These descriptor guarantees apply to the new
v2 backend, not to the legacy v1 path-based implementation.

## Controller views and units

Views exist even if a kernel or subtree lacks their files. Check `has_file`
before optional reads; unsupported reads/writes raise filesystem errors with
the original errno and path.

| View | Properties and methods | Native meaning |
| --- | --- | --- |
| `node.cpu` | `weight`, `max`, `stat`, `pressure` | Weight 1-10000; `max` is `CpuMax(quota_us, period_us)`; `*_usec` stats are microseconds |
| `node.memory` | `current`, `min`, `low`, `high`, `max`, `swap_current`, `swap_max`, `stat`, `events`, `events_local`, `pressure` | Byte counts and byte limits; swap values are swap-only |
| `node.cpuset` | `cpus`, `mems`, `cpus_effective`, `mems_effective` | Requested and effective integer sets; empty requested masks remain empty |
| `node.pids` | `current`, `peak`, `max`, `events`, `events_local` | Kernel task counts, including threads |
| `node.io` | `stat`, `max`, `weight`, `set_weight`, `set_max`, `pressure` | Per-device native values, keyed by `major:minor` |

`cpu.max` quota/period and PSI `total` retain microseconds. PSI `avg10`, `avg60`
and `avg300` are parsed as floats. `io.set_max('8:0', rbps=4096, wbps=None)`
changes only the supplied fields: BPS values are bytes/second and IOPS values
are operations/second. `io.set_weight(100)` changes the default;
`io.set_weight(200, device='8:0')` sends a per-device command.

Parsed kernel `max` values become `None`; writable limit fields serialize
`None` as `max`. The kernel still decides whether a particular file accepts
that value. `CpuMax(None, 100000)` represents unlimited CPU quota with an
explicit period. No shares/weight or nanosecond/microsecond conversion occurs.
Unknown stat/event/keyed fields are retained as integers when lexical integers,
or text otherwise.

Memory-limit writes retain ordinary synchronous kernel reclaim/OOM semantics;
they may block while the kernel applies the requested control.

## Membership, capabilities and lifecycle

* `available_controllers` reads `cgroup.controllers`; `enabled_controllers`
  reads controllers enabled for children through `cgroup.subtree_control`.
* `enable_controllers(['cpu'])` and `disable_controllers(['cpu'])` send explicit
  controller commands. They do not move processes or repair topology.
* `procs` and `threads` return sets. `attach_process`, `attach_processes`,
  `attach_thread`, and `attach_threads` distinguish process and thread commands.
  IDs must be positive integers; zero is not accepted as an implicit self ID.
* `type`, `stat`, and `events` expose native `cgroup.*` interfaces.
* Setting `frozen` sends a request. Observe `events['frozen']` to determine
  completion; the setter is not a synchronous freeze barrier.
* `create_cgroup` and `delete_cgroup` operate on one child name. Deletion is
  non-recursive and the kernel enforces whether the group can be removed.
* `capabilities` includes file presence, available/enabled controllers, and the
  API's write/delegation flags. Those flags are not a kernel permission probe.

## Command batches and failures

`read_raw(filename)` and `write_records(filename, records)` provide access to
native interfaces not covered by typed properties. Filenames are restricted to
one interface name; records are ASCII, one command each. An empty record is
allowed for interfaces such as an empty requested cpuset.

A batch validates every record before mutation, then sends one write syscall
per command. `cgroupspy.v2.BatchWriteError` exposes `completed` (an accepted-record
tuple), `failed_record`, `original_error`, `errno`, and `filename`. There is no
rollback. A failed record may have indeterminate effects after a short write or
I/O error; re-read kernel state before deciding whether to retry. Single-record
typed setters also use this error for command failures. The v1 error has a
different progress attribute; see [compatibility](compatibility.md).

No BPF device-management API, notification loop, PSI trigger writer,
`cgroup.kill`, automatic threaded-topology conversion, or transaction layer is
provided in 0.3.0.

References: [kernel cgroup v2 interfaces](https://docs.kernel.org/admin-guide/cgroup-v2.html),
[systemd delegation](https://systemd.io/CGROUP_DELEGATION/), and
[libvirt cgroup integration](https://libvirt.org/cgroups.html).
