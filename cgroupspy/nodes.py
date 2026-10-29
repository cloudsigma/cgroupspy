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
import logging

import errno
import os

from .controllers import CpuAcctController, CpuController, CpuSetController, MemoryController, DevicesController, \
    BlkIOController, NetClsController, NetPrioController
from .utils import (walk_tree, walk_up_tree, to_bytes, to_text, validate_child_name,
                    is_safe_controller_alias)


LOG = logging.getLogger(__name__)


class Node(object):

    """
    Basic cgroup tree node. Provides means to link it to a parent and set a controller, depending on the cgroup the node
    exists in.
    """
    NODE_ROOT = b"root"
    NODE_CONTROLLER_ROOT = b"controller_root"
    NODE_SLICE = b"slice"
    NODE_SCOPE = b"scope"
    NODE_CGROUP = b"cgroup"

    CONTROLLERS = {
        b"memory": MemoryController,
        b"cpuset": CpuSetController,
        b"cpu": CpuController,
        b"cpuacct": CpuAcctController,
        b"devices": DevicesController,
        b"blkio": BlkIOController,
        b"net_cls": NetClsController,
        b"net_prio": NetPrioController,
    }

    def __init__(self, name, parent=None, controller_types=None, read_only=False):
        if parent is not None and not isinstance(parent, Node):
            raise ValueError('Parent should be another Node')

        name = validate_child_name(name) if parent is not None else to_bytes(name)
        self.name = name
        self.verbose_name = name
        self.parent = parent
        self.read_only = read_only or (parent is not None and parent.read_only)
        self.children = []
        self.node_type = self._get_node_type()
        if controller_types is None:
            if self.node_type == self.NODE_CONTROLLER_ROOT:
                controller_types = name.split(b',')
            elif parent is not None:
                controller_types = parent.controller_types
            else:
                controller_types = ()
        kinds = []
        for value in controller_types:
            value = to_bytes(value)
            if value in self.CONTROLLERS and value not in kinds:
                kinds.append(value)
        self.controller_types = tuple(kinds)
        self.controller_type = self._get_controller_type()
        self.controllers = dict((kind, self.CONTROLLERS[kind](self)) for kind in self.controller_types)
        self.controller = self.controllers.get(self.controller_type)

    def __eq__(self, other):
        if isinstance(other, self.__class__) and self.full_path == other.full_path:
            return True
        return False

    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, to_text(self.path))

    @property
    def full_path(self):
        """Absolute system path to the node"""

        if self.parent:
            return os.path.join(self.parent.full_path, self.name)
        return self.name

    @property
    def path(self):
        """Node's relative path from the root node"""

        if self.parent:
            try:
                parent_path = self.parent.path.encode()
            except AttributeError:
                parent_path = self.parent.path
            return os.path.join(parent_path, self.name)
        return b"/"

    def _get_node_type(self):
        """Returns the current node's type"""

        if self.parent is None:
            return self.NODE_ROOT
        elif self.parent.node_type == self.NODE_ROOT:
            return self.NODE_CONTROLLER_ROOT
        elif b".slice" in self.name or b'.partition' in self.name:
            return self.NODE_SLICE
        elif b".scope" in self.name:
            return self.NODE_SCOPE
        else:
            return self.NODE_CGROUP

    def _get_controller_type(self):
        """Returns the current node's controller type"""

        return self.controller_types[0] if self.controller_types else None

    def _get_controller(self):
        """Returns the current node's controller"""

        if self.controller_type:
            return self.CONTROLLERS[self.controller_type](self)
        return None

    def create_cgroup(self, name):
        """
        Create a cgroup by name and attach it under this node.
        """
        name = validate_child_name(name)
        self._assert_writable()
        node = Node(name, parent=self)
        if node in self.children:
            raise RuntimeError('Node {} already exists under {}'.format(name, self.path))

        fp = os.path.join(self.full_path, name)
        try:
            os.mkdir(fp)
        except OSError as e:
            if e.errno != errno.EEXIST or os.path.islink(fp) or not os.path.isdir(fp):
                raise

        self.children.append(node)
        return node

    def delete_cgroup(self, name):
        """
        Delete a cgroup by name and detach it from this node.
        Raises OSError if the cgroup is not empty.
        """
        name = validate_child_name(name)
        self._assert_writable()
        fp = os.path.join(self.full_path, name)
        try:
            os.rmdir(fp)
        except OSError as error:
            if error.errno != errno.ENOENT:
                raise
        node = Node(name, parent=self)
        try:
            self.children.remove(node)
        except ValueError:
            return

    def delete_empty_children(self):
        """
        Walk through the children of this node and delete any that are empty.
        """
        self._assert_writable()
        removed_children = []

        for child in self.children:
            child.delete_empty_children()
            try:
                os.rmdir(child.full_path)
                removed_children.append(child)
            except OSError as error:
                if error.errno == errno.ENOENT:
                    removed_children.append(child)

        for child in removed_children:
            self.children.remove(child)

    def _assert_writable(self):
        if self.read_only:
            raise OSError(errno.EROFS, 'This cgroup tree is read-only', self.full_path)
        node = self
        while node.parent is not None:
            if os.path.islink(node.full_path):
                if not (node.node_type == self.NODE_CONTROLLER_ROOT and node.controller_types and
                        is_safe_controller_alias(node.full_path, node.parent.full_path)):
                    raise OSError(errno.ELOOP, 'Refusing to mutate through a symlink', node.full_path)
            node = node.parent

    def walk(self):
        """Walk through this node and its children - pre-order depth-first"""
        return walk_tree(self)

    def walk_up(self):
        """Walk through this node and its children - post-order depth-first"""
        return walk_up_tree(self)


class NodeControlGroup(object):

    """
    A tree node that can group together same multiple nodes based on their position in the cgroup hierarchy

    For example - we have mounted all the cgroups in /sys/fs/cgroup/ and we have a scope in each of them under
    /{cpuset,cpu,memory,cpuacct}/isolated.scope/. Then NodeControlGroup, can provide access to all cgroup properties
    like

    isolated_scope.cpu
    isolated_scope.memory
    isolated_scope.cpuset

    Requires a basic Node tree to be generated.
    """

    def __init__(self, name, parent=None):
        self.name = to_bytes(name)
        self.parent = parent
        self.children_map = {}
        self.controllers = {}
        self.nodes = []

    @property
    def path(self):
        if self.parent:
            base_name, ext = os.path.splitext(self.name)
            if ext not in [b'.slice', b'.scope', b'.partition']:
                base_name = self.name
            return os.path.join(self.parent.path, base_name)
        return b"/"

    @property
    def raw_path(self):
        """Exact grouped path, including scope/slice/partition suffixes."""
        if self.parent:
            return os.path.join(self.parent.raw_path, self.name)
        return b'/'

    def add_node(self, node):
        """
        A a Node object to the group. Only one node per cgroup is supported
        """
        new_controllers = {}
        physical_path = os.path.realpath(node.full_path)
        for kind, controller in node.controllers.items():
            existing = self.controllers.get(kind)
            if existing is not None:
                if os.path.realpath(existing.node.full_path) == physical_path:
                    continue
                raise RuntimeError("Cannot add node {} to the node group. A node for {} group is already assigned".format(
                    node, kind))
            new_controllers[kind] = controller
        if not new_controllers and any(os.path.realpath(existing.full_path) == physical_path
                                       for existing in self.nodes):
            return
        self.nodes.append(node)
        for kind, controller in new_controllers.items():
            self.controllers[kind] = controller
            setattr(self, to_text(kind), controller)

    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, to_text(self.name))

    @property
    def children(self):
        return list(self.children_map.values())

    @property
    def group_tasks(self):
        """All tasks in the hierarchy, affected by this group."""
        tasks = set()
        for node in walk_tree(self):
            for ctrl in node.controllers.values():
                tasks.update(ctrl.tasks)
        return tasks

    @property
    def tasks(self):
        """Tasks in this exact group"""
        tasks = set()
        for ctrl in self.controllers.values():
            tasks.update(ctrl.tasks)
        return tasks


class NodeVM(NodeControlGroup):

    """Abstraction of a QEMU virtual machine node."""

    @property
    def verbose_name(self):
        try:
            return self.nodes[0].verbose_name
        except Exception:
            return "AnonymousVM"

    @property
    def emulator(self):
        direct = self.children_map.get(b"emulator")
        if direct is not None:
            return direct
        libvirt = self.children_map.get(b'libvirt')
        return libvirt.children_map.get(b'emulator') if libvirt is not None else None

    @property
    def vcpus(self):
        direct = [node for name, node in self.children_map.items() if name.startswith(b"vcpu")]
        if direct:
            return direct
        libvirt = self.children_map.get(b'libvirt')
        if libvirt is None:
            return []
        return [node for name, node in libvirt.children_map.items() if name.startswith(b'vcpu')]
