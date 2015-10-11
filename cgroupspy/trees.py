"""
Copyright (c) 2014, CloudSigma AG
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the CloudSigma AG nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL CLOUDSIGMA AG BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import os

from .nodes import Node, NodeControlGroup, NodeVM
from .utils import walk_tree, walk_up_tree, split_path_components, mount


def bootstrap(root_path=b"/sys/fs/cgroup/", subsystems=None):
    if not os.path.ismount(root_path):
        if not os.path.isdir(root_path):
            os.makedirs(root_path)
        try:
            mount(b"cgroup_root", root_path, b"tmpfs")
        except RuntimeError:
            os.rmdir(root_path)
            raise
    if subsystems is None:
        subsystems = Node.CONTROLLERS.keys()
    for subsystem in subsystems:
        path = root_path+subsystem
        if not os.path.ismount(path):
            if not os.path.isdir(path):
                os.makedirs(path)
            try:
                mount(subsystem, path, b"cgroup", subsystem)
            except RuntimeError:
                os.rmdir(path)
                raise


class BaseTree(object):

    """ A basic cgroup node tree. An exact representation of the filesystem tree, provided by cgroups. """

    def __init__(self, root_path="/sys/fs/cgroup/", groups=None, sub_groups=None):
        self.root_path = root_path
        self._groups = groups or []
        self._sub_groups = sub_groups or []
        self.root = Node(root_path)
        self._build_tree()

    @property
    def groups(self):
        return self._groups

    def _build_tree(self):
        """
        Build a full or a partial tree, depending on the groups/sub-groups specified.
        """

        groups = self._groups or self.get_children_paths(self.root_path)
        for group in groups:
            node = Node(name=group, parent=self.root)
            self.root.children.append(node)
            self._init_sub_groups(node)

    def _init_sub_groups(self, parent):
        """
        Initialise sub-groups, and create any that do not already exist.
        """

        if self._sub_groups:
            for sub_group in self._sub_groups:
                for component in split_path_components(sub_group):
                    fp = os.path.join(parent.full_path, component)
                    if os.path.exists(fp):
                        node = Node(name=component, parent=parent)
                        parent.children.append(node)
                    else:
                        node = parent.create_cgroup(component)
                    parent = node
                self._init_children(node)
        else:
            self._init_children(parent)

    def _init_children(self, parent):
        """
        Initialise each node's children - essentially build the tree.
        """

        for dir_name in self.get_children_paths(parent.full_path):
            child = Node(name=dir_name, parent=parent)
            parent.children.append(child)
            self._init_children(child)

    def get_children_paths(self, parent_full_path):
        for dir_name in os.listdir(parent_full_path):
            if os.path.isdir(os.path.join(parent_full_path, dir_name)):
                yield dir_name

    def walk(self, root=None):
        """Walk through each each node - pre-order depth-first"""

        if root is None:
            root = self.root
        return walk_tree(root)

    def walk_up(self, root=None):
        """Walk through each each node - post-order depth-first"""

        if root is None:
            root = self.root
        return walk_up_tree(root)


class Tree(BaseTree):
    def get_node_by_path(self, path):
        try:
            path = path.encode()
        except AttributeError:
            pass
        path = path.rstrip(b"/")
        for node in self.walk():
            if node.path == path:
                return node


class GroupedTree(object):
    """
    A grouped tree - that has access to all cgroup partitions with the same name ex:
    'machine' partition in memory, cpuset, cpus, etc cgroups.
    All these attributes are accessed via machine.cpus, machine.cpuset, etc.

    """

    def __init__(self, root_path=b"/sys/fs/cgroup", groups=None, sub_groups=None):

        self.node_tree = BaseTree(root_path=root_path, groups=groups, sub_groups=sub_groups)
        self.control_root = NodeControlGroup(name=b"cgroup")
        for ctrl in self.node_tree.root.children:
            self.control_root.add_node(ctrl)

        self._init_control_tree(self.control_root)

    def _init_control_tree(self, cgroup):
        new_cgroups = []
        for node in cgroup.nodes:

            for child in node.children:
                if child.name not in cgroup.children_map:
                    new_cgroup = self._create_node(child.verbose_name, parent=cgroup)
                    cgroup.children_map[child.name] = new_cgroup
                    new_cgroups.append(new_cgroup)

                cgroup.children_map[child.name].add_node(child)

        for new_group in new_cgroups:
            self._init_control_tree(new_group)

    def _create_node(self, name, parent):
        return NodeControlGroup(name, parent=parent)

    def walk(self, root=None):
        if root is None:
            root = self.control_root
        return walk_tree(root)

    def walk_up(self, root=None):
        if root is None:
            root = self.control_root
        return walk_up_tree(root)

    def get_node_by_name(self, pattern):
        try:
            pattern = pattern.encode()
        except AttributeError:
            pass
        for node in self.walk():
            if pattern in node.name:
                return node

    def get_node_by_path(self, path):
        try:
            path = path.encode()
        except AttributeError:
            pass
        for node in self.walk():
            if path == node.path:
                return node


class VMTree(GroupedTree):

    def __init__(self, *args, **kwargs):
        self.vms = {}
        super(VMTree, self).__init__(*args, **kwargs)

    def _create_node(self, name, parent):
        if b"libvirt-qemu" in name or parent.name == b"qemu":
            vm_node = NodeVM(name, parent=parent)
            self.vms[name] = vm_node
            return vm_node
        return super(VMTree, self)._create_node(name, parent=parent)

    def get_vm_node(self, name):
        return self.vms.get(b'%s.libvirt-qemu' % (name))
