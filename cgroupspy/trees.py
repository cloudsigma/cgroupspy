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
import errno
import stat

from .nodes import Node, NodeControlGroup, NodeVM
from .utils import (walk_tree, walk_up_tree, split_path_components, to_bytes,
                    to_text, validate_child_name, normalize_tree_path)
from .utils import is_safe_controller_alias


class BaseTree(object):

    """ A basic cgroup node tree. An exact representation of the filesystem tree, provided by cgroups. """

    def __init__(self, root_path=b"/sys/fs/cgroup/", groups=None, sub_groups=None,
                 create_missing=True, read_only=False, controller_map=None):
        """
        Construct a basic cgroup node tree. An exact representation of the filesystem tree, provided by cgroups.

        :param root_path: str -> The path of the root folder containing the cgroups. By default it is /sys/fs/cgroup/
        :param groups: None | list -> Use only those controllers to collect information in this tree instance
        :param sub_groups: None | list -> Use only those slices to retrieve information. If the slice does not exist,
                                          then create it
        :param create_missing: Create missing requested subgroups (legacy default: True)
        :param read_only: Disable creation and mutation through this tree
        :param controller_map: Optional mount-directory -> controller-names mapping for custom v1 mounts
        """
        self.root_path = to_bytes(root_path)
        self._groups = list(groups or [])
        for group in self._groups:
            validate_child_name(group)
        self._sub_groups = []
        for sub_group in sub_groups or []:
            sub_group = sub_group.strip()
            components = split_path_components(sub_group)
            for component in components:
                validate_child_name(component)
            if components and sub_group not in self._sub_groups:
                self._sub_groups.append(sub_group)
        self.read_only = bool(read_only)
        self.create_missing = bool(create_missing) and not self.read_only
        self.controller_map = dict((to_bytes(key), tuple(to_bytes(value) for value in values))
                                   for key, values in (controller_map or {}).items())
        self.root = Node(self.root_path, read_only=self.read_only)
        self._build_tree()

    def refresh(self):
        """Rebuild a filesystem snapshot; previously returned nodes remain old snapshots.

        The original subgroup creation policy also applies to refresh. Construction
        and refresh are best-effort scans, not atomic views of process lifetimes.
        """
        old_root = self.root
        self.root = Node(self.root_path, read_only=self.read_only)
        try:
            self._build_tree()
        except Exception:
            self.root = old_root
            raise
        return self

    @property
    def groups(self):
        return self._groups

    @property
    def sub_groups(self):
        return self._sub_groups

    def _build_tree(self):
        """
        Build a full or a partial tree, depending on the groups/sub-groups specified.
        """

        if not stat.S_ISDIR(os.stat(self.root_path).st_mode):
            raise OSError(errno.ENOTDIR, 'The cgroup root is not a directory', self.root_path)
        groups = self._groups or self.get_children_paths(self.root_path)
        for group in groups:
            node = Node(name=group, parent=self.root, controller_types=self.controller_map.get(to_bytes(group)))
            if os.path.islink(node.full_path) and not (node.controller_types and
                    is_safe_controller_alias(node.full_path, self.root_path)):
                raise OSError(errno.ELOOP, 'Refusing to follow a controller symlink outside the root', node.full_path)
            self.root.children.append(node)
            self._init_sub_groups(node)

    def _init_sub_groups(self, parent):
        """
        Initialise sub-groups, and create any that do not already exist.
        """
        if not self._sub_groups:
            self._init_children(parent)
            return

        for sub_group in self._sub_groups:
            current = parent
            complete = True
            for component in split_path_components(sub_group):
                component = validate_child_name(component)
                node = next((child for child in current.children if child.name == component), None)
                if node is None:
                    fp = os.path.join(current.full_path, component)
                    if os.path.islink(fp):
                        raise OSError(errno.ELOOP, 'Refusing to follow a subgroup symlink', fp)
                    if os.path.isdir(fp):
                        node = Node(name=component, parent=current)
                        current.children.append(node)
                    elif self.create_missing:
                        try:
                            node = current.create_cgroup(component)
                        except OSError as error:
                            if error.errno not in (errno.ENOENT, errno.ENOTDIR):
                                raise
                            complete = False
                            break
                    else:
                        complete = False
                        break
                current = node
            if complete:
                self._init_children(current)

    def _init_children(self, parent):
        """
        Initialise each node's children - essentially build the tree.
        """

        for dir_name in self.get_children_paths(parent.full_path):
            child = next((node for node in parent.children if node.name == dir_name), None)
            if child is None:
                child = Node(name=dir_name, parent=parent)
                parent.children.append(child)
            self._init_children(child)
            if not os.path.isdir(child.full_path) or os.path.islink(child.full_path):
                parent.children.remove(child)

    def get_children_paths(self, parent_full_path):
        if parent_full_path != self.root_path and os.path.islink(parent_full_path):
            if not self._is_controller_alias(parent_full_path):
                return
        try:
            names = os.listdir(parent_full_path)
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ENOTDIR) and parent_full_path != self.root_path:
                return
            raise
        for dir_name in names:
            fp = os.path.join(parent_full_path, dir_name)
            if os.path.islink(fp):
                if parent_full_path == self.root_path and self._is_controller_alias(fp):
                    yield dir_name
            elif os.path.isdir(fp):
                yield dir_name

    def _is_controller_alias(self, path):
        name = os.path.basename(path)
        kinds = self.controller_map.get(name, name.split(b','))
        return (os.path.abspath(os.path.dirname(path)) == os.path.abspath(self.root_path) and
                any(kind in Node.CONTROLLERS for kind in kinds) and
                is_safe_controller_alias(path, self.root_path))

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
        path = normalize_tree_path(path)
        for node in self.walk():
            if node.path == path:
                return node


class GroupedTree(object):
    """
    A grouped tree - that has access to all cgroup partitions with the same name ex:
    'machine' partition in memory, cpuset, cpus, etc cgroups.
    All these attributes are accessed via machine.cpus, machine.cpuset, etc.

    """

    def __init__(self, root_path=b"/sys/fs/cgroup", groups=None, sub_groups=None,
                 create_missing=True, read_only=False, controller_map=None):

        self.node_tree = BaseTree(root_path=root_path, groups=groups, sub_groups=sub_groups,
                                  create_missing=create_missing, read_only=read_only,
                                  controller_map=controller_map)
        self._build_control_tree()

    def _build_control_tree(self):
        self.control_root = NodeControlGroup(name=b"cgroup")
        # Prefer a co-mounted directory with all logical controller views over
        # its individual aliases, retaining the order of unrelated hierarchies.
        # The underlying filesystem tree keeps every logical filesystem path.
        physical_groups = []
        positions = {}
        for ctrl in self.node_tree.root.children:
            physical_path = os.path.realpath(ctrl.full_path)
            if physical_path not in positions:
                positions[physical_path] = len(physical_groups)
                physical_groups.append([])
            physical_groups[positions[physical_path]].append(ctrl)
        for aliases in physical_groups:
            for ctrl in sorted(aliases, key=lambda node: -len(node.controllers)):
                self.control_root.add_node(ctrl)

        self._init_control_tree(self.control_root)

    def refresh(self):
        """Refresh both grouped and filesystem snapshots; existing handles stay snapshots."""
        old_root = self.node_tree.root
        old_control_root = self.control_root
        try:
            self.node_tree.refresh()
            self._build_control_tree()
        except Exception:
            self.node_tree.root = old_root
            self.control_root = old_control_root
            raise
        return self

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
        pattern = to_bytes(pattern)
        for node in self.walk():
            if pattern in node.name:
                return node

    def get_node_by_path(self, path):
        """Return the first legacy normalized match; use raw paths to avoid collisions."""
        path = normalize_tree_path(path)
        for node in self.walk():
            if path == node.path:
                return node

    def get_nodes_by_path(self, path):
        """Return all matches for a legacy path that may omit scope/slice suffixes."""
        path = normalize_tree_path(path)
        return [node for node in self.walk() if node.path == path]

    def get_node_by_raw_path(self, path):
        """Look up an exact grouped filesystem path, retaining every suffix."""
        path = normalize_tree_path(path)
        for node in self.walk():
            if node.raw_path == path:
                return node


class VMTree(GroupedTree):

    def __init__(self, *args, **kwargs):
        self.vms = {}
        super(VMTree, self).__init__(*args, **kwargs)

    def refresh(self):
        old_vms = self.vms
        self.vms = {}
        try:
            super(VMTree, self).refresh()
        except Exception:
            self.vms = old_vms
            raise
        return self

    def _create_node(self, name, parent):
        if b"libvirt-qemu" in name or b"machine-qemu" in name or parent.name == b"qemu":
            vm_node = NodeVM(name, parent=parent)
            key = to_text(name)
            self.vms[key] = vm_node
            return vm_node
        return super(VMTree, self)._create_node(name, parent=parent)

    def get_vm_node(self, name):
        name = to_text(name)
        keys = [
            name,
            '%s.libvirt-qemu' % name,
            "machine-qemu%s" % name
        ]

        for key in keys:
            if key in self.vms:
                return self.vms[key]
