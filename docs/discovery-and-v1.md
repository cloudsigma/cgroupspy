# Discovery and the v1 tree API

## Read-only mount discovery

```python
from cgroupspy.discovery import controller_map, discover
from cgroupspy.trees import Tree

hierarchies = discover()
for hierarchy in hierarchies:
    print(hierarchy.version, hierarchy.mountpoint, hierarchy.current_path)

tree = Tree('/sys/fs/cgroup', read_only=True,
            controller_map=controller_map(hierarchies, '/sys/fs/cgroup'))
```

`discover(mountinfo=None, cgroup=None)` reads `/proc/self/mountinfo` and
`/proc/self/cgroup` in the calling process's namespace. It returns `Hierarchy`
objects with `version`, byte-valued `mountpoint`, `mount_root`, optional
`process_path`, controller-name set, mount options, `read_only`, and
`current_path`. The latter is `None` when membership cannot safely be resolved
under that mount; the function does not guess across namespace boundaries.

Inputs may be injected as text, bytes, or readable file objects for tests/offline
inspection. Supplying mountinfo alone does not mix it with live process
membership. Escaped mount paths, bind-mounted roots, co-mounted v1 controllers,
and mixed v1/v2 mounts are represented. V2 controller availability comes from
`V2Node.available_controllers`, not mount options.

`controller_map(hierarchies, root_path)` maps immediate v1 mount directories to
controller-name tuples. Nested, unrelated, and v2 mountpoints are omitted.
This is an explicit adapter for custom mount names: constructing a legacy tree
does not automatically read `/proc`, and discovery does not enable controllers
or create directories. Use `V2Tree` for a v2 hierarchy.

## Legacy constructors and controller views

The signatures of `Tree`, `GroupedTree`, and the inherited `VMTree` constructor
retain their existing positional arguments and append optional settings:

```python
Tree(root_path=b'/sys/fs/cgroup/', groups=None, sub_groups=None,
     create_missing=True, read_only=False, controller_map=None)
```

`GroupedTree` keeps its historical default root spelling
`b'/sys/fs/cgroup'`. Names and filesystem/relative node paths remain bytes.
Inputs accept text and bytes. Existing exact mounts retain their primary
`node.controller` and `node.controller_type` values. Additive
`node.controllers` is a bytes-keyed mapping of logical controller views, and
`node.controller_types` is their ordered tuple. For a `cpu,cpuacct` directory,
the primary view is the first recognized controller, while grouped nodes expose
both `.cpu` and `.cpuacct`.

Default filesystem trees retain safe immediate controller aliases such as
`cpu -> cpu,cpuacct`, including `/cpu` lookups. Grouped views prefer complete
co-mounted nodes and deduplicate the same physical controller. Different
physical directories claiming the same grouped controller still conflict.
Unrelated hierarchy order is retained. Nested symlinks are not traversed;
aliases escaping the configured root are excluded, or rejected when explicitly
requested. Valid explicit aliases resolve to an immediate child of that root.

## Subgroups, writes and refresh

The legacy default **creates missing requested subgroups during construction**.
Multiple requested subgroups are now siblings, and shared prefixes reuse the
same nodes. `create_missing=False` skips missing requested branches without
creating them; it does not disable later explicit mutation.

`read_only=True` implies no missing-subgroup creation and blocks node creation,
deletion, cleanup and controller writes through that tree. It is an API policy,
not an operating-system permission boundary. Legacy v1 file operations remain
path-based; they do not gain the v2 backend's descriptor anchoring.

`refresh()` rebuilds filesystem and grouped snapshots and returns the tree.
Previously returned node handles remain old snapshots. Refresh reapplies the
original subgroup-creation policy, so a writable tree with `create_missing=True`
can recreate a missing requested subgroup. A failed refresh retains the previous
snapshot. Scans tolerate expected descendant disappearance but propagate root,
permission and other unexpected errors. Neither scans nor refresh are atomic.

## Paths, collisions and VMs

Root and trailing-slash lookups work with text or bytes. The existing grouped
`path` still strips `.scope`, `.slice`, and `.partition` suffixes; two different
groups may therefore share one historical path. `get_node_by_path` retains
first-match behavior. New `get_nodes_by_path` returns all normalized matches,
while `raw_path` and `get_node_by_raw_path` retain exact directory names:

```python
from cgroupspy.trees import GroupedTree

tree = GroupedTree(read_only=True)
matches = tree.get_nodes_by_path('/machine/example')
exact = tree.get_node_by_raw_path('/machine.slice/example.scope')
```

`VMTree` retains historical `vms` keys and `get_vm_node` fallbacks, accepting
bytes as well as text lookup names. VM identity still relies on directory-name
conventions; duplicate names in different partitions can share a legacy key.
Use exact raw paths when identity is ambiguous. Scope names do not reliably
encode a recoverable guest UUID.

`NodeVM.emulator` and `.vcpus` preserve direct-child lookup and fall back to an
optional `libvirt` child for modern layouts. The VM node remains the aggregate
scope. No libvirt API, systemd adapter or guest-UUID resolver is added.
