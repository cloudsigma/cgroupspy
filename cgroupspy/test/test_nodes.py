import errno
import os
import shutil
import tempfile
from unittest import TestCase

from cgroupspy.controllers import CpuAcctController, CpuController
from cgroupspy.nodes import Node, NodeControlGroup, NodeVM


class TestNodes(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.root = Node(self.directory)

    def test_create_delete_round_trip_accepts_text_and_bytes(self):
        for name in ('first', b'second'):
            child = self.root.create_cgroup(name)
            self.assertIsInstance(child.name, bytes)
            self.assertIsInstance(child.path, bytes)
            self.root.delete_cgroup(child.name)
            self.assertFalse(os.path.exists(child.full_path))
            self.assertEqual(self.root.children, [])

    def test_unsafe_names_are_rejected_before_mutation(self):
        for name in ('', '.', '..', '../escape', '/absolute', 'nested/child', 'bad\x00name'):
            with self.assertRaises(ValueError):
                self.root.create_cgroup(name)
            with self.assertRaises(ValueError):
                self.root.delete_cgroup(name)
        self.assertEqual(os.listdir(self.directory), [])

    def test_existing_directory_is_attached_but_cached_duplicate_fails(self):
        os.mkdir(os.path.join(self.directory, 'existing'))
        child = self.root.create_cgroup('existing')
        self.assertEqual(child.path, b'/existing')
        with self.assertRaises(RuntimeError):
            self.root.create_cgroup('existing')

    def test_existing_regular_file_is_not_a_cgroup(self):
        with open(os.path.join(self.directory, 'file'), 'w'):
            pass
        with self.assertRaises(OSError):
            self.root.create_cgroup('file')
        self.assertEqual(self.root.children, [])

    def test_symlink_cannot_be_attached_or_used_for_mutation(self):
        destination = os.path.join(self.directory, 'destination')
        os.mkdir(destination)
        os.symlink(destination, os.path.join(self.directory, 'link'))
        with self.assertRaises(OSError):
            self.root.create_cgroup('link')
        linked = Node('link', parent=self.root)
        with self.assertRaises(OSError):
            linked.create_cgroup('child')
        self.assertEqual(os.listdir(destination), [])

    def test_delete_detaches_a_directory_already_removed_externally(self):
        child = self.root.create_cgroup('gone')
        os.rmdir(child.full_path)
        self.root.delete_cgroup(b'gone')
        self.assertEqual(self.root.children, [])

    def test_cleanup_detaches_disappeared_children_and_retains_nonempty(self):
        gone = self.root.create_cgroup('gone')
        keep = self.root.create_cgroup('keep')
        with open(os.path.join(keep.full_path, b'file'), 'w'):
            pass
        os.rmdir(gone.full_path)
        self.root.delete_empty_children()
        self.assertEqual(self.root.children, [keep])

    def test_read_only_policy_is_inherited(self):
        root = Node(self.directory, read_only=True)
        child = Node('cpu', parent=root)
        self.assertTrue(child.read_only)
        for operation in (lambda: child.create_cgroup('new'),
                          lambda: child.delete_cgroup('old'), child.delete_empty_children):
            try:
                operation()
            except OSError as error:
                self.assertEqual(error.errno, errno.EROFS)
            else:
                self.fail('A read-only mutation succeeded')

    def test_combined_controller_views_and_legacy_primary(self):
        cpu = Node('cpu', parent=self.root)
        self.assertEqual(cpu.controller_type, b'cpu')
        self.assertIsInstance(cpu.controller, CpuController)
        combined = Node('cpu,cpuacct', parent=self.root)
        child = Node('guest', parent=combined)
        self.assertEqual(combined.controller_types, (b'cpu', b'cpuacct'))
        self.assertEqual(child.controller_types, combined.controller_types)
        self.assertIsInstance(child.controllers[b'cpu'], CpuController)
        self.assertIsInstance(child.controllers[b'cpuacct'], CpuAcctController)
        group = NodeControlGroup('guest')
        group.add_node(child)
        self.assertIs(group.cpu, child.controllers[b'cpu'])
        self.assertIs(group.cpuacct, child.controllers[b'cpuacct'])

    def test_grouped_raw_path_preserves_names(self):
        root = NodeControlGroup(b'root')
        parent = NodeControlGroup('machine.slice', parent=root)
        child = NodeControlGroup('guest.scope', parent=parent)
        self.assertEqual(child.path, b'/machine/guest')
        self.assertEqual(child.raw_path, b'/machine.slice/guest.scope')

    def test_vm_thread_helpers_accept_direct_and_nested_libvirt_layouts(self):
        vm = NodeVM(b'guest')
        libvirt = NodeControlGroup(b'libvirt', parent=vm)
        nested_emulator = NodeControlGroup(b'emulator', parent=libvirt)
        nested_vcpu = NodeControlGroup(b'vcpu0', parent=libvirt)
        vm.children_map[b'libvirt'] = libvirt
        libvirt.children_map.update({b'emulator': nested_emulator, b'vcpu0': nested_vcpu})
        self.assertIs(vm.emulator, nested_emulator)
        self.assertEqual(vm.vcpus, [nested_vcpu])
        direct_emulator = NodeControlGroup(b'emulator', parent=vm)
        direct_vcpu = NodeControlGroup(b'vcpu1', parent=vm)
        vm.children_map.update({b'emulator': direct_emulator, b'vcpu1': direct_vcpu})
        self.assertIs(vm.emulator, direct_emulator)
        self.assertEqual(vm.vcpus, [direct_vcpu])
