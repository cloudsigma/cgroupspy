cgroupspy
=========

Python library for managing cgroups

The library provides a pythonic way to manage and represent cgroups. It provides interfaces that convert
python objects to cgroups compatible strings and vise versa.

Version 0.3.0 adds explicit cgroup v2 access, namespace-aware mount discovery,
and fixes for the existing v1 API. Existing `Tree`, `GroupedTree`, `VMTree`,
controller names, byte paths, and default v1 subgroup-creation behavior are
retained. The runtime contract remains Python 2.7 and Python 3.6 or newer;
Linux is required for actual cgroup access.

Use the existing `cgroupspy.trees` APIs for v1 hierarchies and
`cgroupspy.v2.V2Tree` for a unified v2 hierarchy. Their resource units and
controller semantics remain distinct.

Install the 0.3.0 GitHub release
-------------------------------

```sh
python -m pip install "https://github.com/cloudsigma/cgroupspy/releases/download/v0.3.0/cgroupspy-0.3.0-py2.py3-none-any.whl"
```

The [versioned release](https://github.com/cloudsigma/cgroupspy/releases/tag/v0.3.0)
also contains the source archive and SHA-256 checksums. GitHub release files are
available independently of PyPI publication; check the installed package version
before using the new API.

Read a cgroup v2 subtree
-----------------------

```python
from cgroupspy.v2 import V2Tree

# Select an existing, namespace-visible cgroup v2 mount or subtree.
with V2Tree('/sys/fs/cgroup') as tree:
    print(tree.root.available_controllers)
    node = tree.get_node_by_path('/machine.slice')
    if node is not None and node.has_file('memory.current'):
        print(node.memory.current)  # bytes
    if node is not None and node.has_file('cpu.stat'):
        print(node.cpu.stat.get('usage_usec'))  # microseconds
```

The v2 default is read-only. Writing requires an explicitly selected delegated
subtree and both `writable=True` and `delegated=True`. The delegation flag is a
caller assertion, not proof of ownership or a privilege grant. Construction
does not create groups, enable controllers, or move processes.

Documentation:

* [Native cgroup v2 API and units](docs/cgroup-v2.md)
* [Mount discovery and the v1 tree API](docs/discovery-and-v1.md)
* [Compatibility and intentional input hardening](docs/compatibility.md)
* [Tests, distribution checks, and Linux integration](docs/testing.md)
* [Changelog](CHANGELOG.md)

The examples below describe the existing v1 API. Available controllers, VM
names, and displayed values depend on the host. Turlo's private GitLab source
was unavailable during this update; compatibility with Turlo is not certified.


Trees
-----
cgroupspy has a couple of ways to represent the cgroups filesystem

* As a tree - this is the most basic and generic way to represent them. You basically construct it from all
the directories in the cgroups root.

* A grouped tree - that has access to all cgroup partitions with the same name, on the same level. For example -
'machine' partition in memory, cpuset, cpus, etc cgroups. All these attributes are
accessed via machine.cpus, machine.cpuset, etc.

* A VMTree - a subclass of grouped tree with utilities for simple management of libvirt guests

Example usage
-------------
```python
#Import the trees module, which contains a tree representation of cgroups
>>> from cgroupspy import trees

# This is the most basic type of cgroup tree. It models the filesystem.
>>> t = trees.Tree()

# It has a root which is of type Node
>>> t.root
<Node />

# And the root has children
>>> print(t.root.children)
[<Node /hugetlb>, <Node /net_prio>, <Node /perf_event>, <Node /blkio>, <Node /net_cls>, <Node /freezer>, <Node /devices>, <Node /memory>, <Node /cpuacct>, <Node /cpu>, <Node /cpuset>, <Node /systemd>, <Node /cgmanager>]

# You can for example get the cpuset
>>> cset = t.get_node_by_path('/cpuset/')
>>> cset
<Node /cpuset>

# The controller used for this cgroup is a CpuSetController
>>> cset.controller
<cgroupspy.controllers.CpuSetController object at 0x7f63a3843050>

# Which can for example show you the cpu pinning
>>> cset.controller.cpus
set([0, 1])

# You can create a cgroup
>>> test = cset.create_cgroup('test')
<Node /cpuset/test>

# See its cpu restrictions
>>> test.controller.cpus
set([0, 1])

# And change them
>>> test.controller.cpus = [1]

# The tasks in this cgroup are now restricted to cpu 1
>>> test.controller.cpus
set([1])
```

Another example with the VMTree - for managing libvirt guests

```python
>>> from cgroupspy.trees import VMTree
>>> vmt = VMTree()
>>> print(vmt.vms)
{u'1ce10f47-fb4e-4b6a-8ee6-ba34940cdda7.libvirt-qemu': <NodeVM 1ce10f47-fb4e-4b6a-8ee6-ba34940cdda7.libvirt-qemu>,
 u'3d5013b9-93ed-4ef1-b518-a2cea43f69ad.libvirt-qemu': <NodeVM 3d5013b9-93ed-4ef1-b518-a2cea43f69ad.libvirt-qemu>,
}

>>> vm = vmt.get_vm_node("1ce10f47-fb4e-4b6a-8ee6-ba34940cdda7")
>>> print(vm.cpu.shares)
1024
>>> print(vm.cpuset.cpus)
{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
>>> print(vm.memory.limit_in_bytes)
25603080192
>>> print(vm.children)
[<NodeControlGroup vcpu1>,
 <NodeControlGroup vcpu0>,
 <NodeControlGroup emulator>]
>>> print(vm.path)
/machine/grey/1ce10f47-fb4e-4b6a-8ee6-ba34940cdda7.libvirt-qemu
>>> vcpu1 = vm.children[0]
>>> print(vcpu1.cpuset.cpus)
{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}

>>> vcpu1.cpuset.cpus = {1,2,3}

>>> print(vcpu1.cpuset.cpus)
{1, 2, 3}
```

License
-------
new BSD licence


