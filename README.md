[![Build Status](https://travis-ci.org/cloudsigma/cgroupspy.svg)](https://travis-ci.org/cloudsigma/cgroupspy)
cgroupspy
=========

Python library for managing cgroups

The library provides a pythonic way to manage and represent cgroups. It provides interfaces that convert
python objects to cgroups compatible strings and vise versa.


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


