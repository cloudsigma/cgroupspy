import errno
import os
import shutil
import tempfile
from unittest import TestCase

import mock

from cgroupspy.trees import GroupedTree, Tree, VMTree
from cgroupspy.utils import to_bytes


class TestTrees(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def mkdir(self, path):
        os.makedirs(os.path.join(self.directory, path))

    def test_subgroups_are_siblings_across_controllers(self):
        self.mkdir('cpu')
        self.mkdir('memory')
        tree = Tree(self.directory, groups=['cpu', 'memory'], sub_groups=['a', 'b'])
        self.assertEqual(tree.groups, ['cpu', 'memory'])
        for controller in ('cpu', 'memory'):
            self.assertIsNotNone(tree.get_node_by_path('/' + controller + '/a'))
            self.assertIsNotNone(tree.get_node_by_path('/' + controller + '/b'))
            self.assertFalse(os.path.exists(os.path.join(self.directory, controller, 'a', 'b')))

    def test_shared_prefixes_and_duplicate_subgroups_reuse_nodes(self):
        self.mkdir('cpu')
        tree = Tree(self.directory, sub_groups=['a/b', 'a/c', 'a', 'a/b'])
        cpu = tree.get_node_by_path('/cpu')
        self.assertEqual([n.name for n in cpu.children], [b'a'])
        self.assertEqual(set(n.name for n in cpu.children[0].children), {b'b', b'c'})
        self.assertEqual(len(list(tree.walk())), 5)

    def test_bytes_and_empty_subgroups(self):
        self.mkdir('cpu/existing')
        tree = Tree(to_bytes(self.directory), sub_groups=[b' ', b'/', b'/existing/'])
        self.assertIsNotNone(tree.get_node_by_path(b'/cpu/existing/'))
        self.assertEqual(tree.sub_groups, [b'/existing/'])

    def test_unsafe_groups_are_validated_before_creating_anything(self):
        self.mkdir('cpu')
        for options in ({'groups': ['../cpu']}, {'sub_groups': ['valid', '../escape']},
                        {'sub_groups': ['a/./b']}, {'sub_groups': ['bad\x00name']}):
            with self.assertRaises(ValueError):
                Tree(self.directory, **options)
        self.assertEqual(os.listdir(os.path.join(self.directory, 'cpu')), [])

    def test_read_only_or_create_missing_false_does_not_create_subgroups(self):
        self.mkdir('cpu/existing')
        for options in ({'read_only': True}, {'create_missing': False}):
            tree = Tree(self.directory, sub_groups=['existing', 'absent'], **options)
            self.assertIsNotNone(tree.get_node_by_path('/cpu/existing'))
            self.assertIsNone(tree.get_node_by_path('/cpu/absent'))
            self.assertFalse(os.path.exists(os.path.join(self.directory, 'cpu', 'absent')))

    def test_subgroup_symlinks_are_rejected(self):
        self.mkdir('cpu/actual')
        os.symlink('actual', os.path.join(self.directory, 'cpu', 'link'))
        with self.assertRaises(OSError):
            Tree(self.directory, sub_groups=['link/child'])
        self.assertFalse(os.path.exists(os.path.join(self.directory, 'cpu', 'actual', 'child')))

    def test_root_and_trailing_slash_lookups(self):
        self.mkdir('cpu/guest')
        tree = Tree(self.directory)
        self.assertIs(tree.get_node_by_path('/'), tree.root)
        self.assertIs(tree.get_node_by_path(b'/'), tree.root)
        self.assertEqual(tree.get_node_by_path('/cpu/guest/').path, b'/cpu/guest')
        self.assertIsNone(tree.get_node_by_path(''))

    def test_disappearing_descendant_does_not_abort_scan(self):
        self.mkdir('cpu/gone')
        gone = to_bytes(os.path.join(self.directory, 'cpu', 'gone'))
        original = os.listdir

        def listdir(path):
            if path == gone:
                os.rmdir(path)
            return original(path)

        with mock.patch('cgroupspy.trees.os.listdir', side_effect=listdir):
            tree = Tree(self.directory)
        self.assertIsNone(tree.get_node_by_path('/cpu/gone'))

    def test_permission_errors_are_not_hidden(self):
        self.mkdir('cpu/denied')
        denied = to_bytes(os.path.join(self.directory, 'cpu', 'denied'))
        original = os.listdir

        def listdir(path):
            if path == denied:
                raise OSError(errno.EACCES, 'denied')
            return original(path)

        with mock.patch('cgroupspy.trees.os.listdir', side_effect=listdir):
            with self.assertRaises(OSError):
                Tree(self.directory)

    def test_missing_root_is_an_error(self):
        with self.assertRaises(OSError):
            Tree(os.path.join(self.directory, 'missing'))
        with self.assertRaises(OSError):
            Tree(os.path.join(self.directory, 'missing'), groups=['cpu'])

    def test_discovery_does_not_recurse_through_symlinks(self):
        self.mkdir('cpu/guest')
        os.symlink('..', os.path.join(self.directory, 'cpu', 'cycle'))
        self.assertEqual(len(list(Tree(self.directory).walk())), 3)

    def test_combined_mounts_and_custom_controller_mapping(self):
        self.mkdir('cpu,cpuacct/guest')
        self.mkdir('ram/guest')
        tree = GroupedTree(self.directory, controller_map={'ram': ['memory']})
        guest = tree.get_node_by_raw_path('/guest')
        self.assertEqual(set(guest.controllers), {b'cpu', b'cpuacct', b'memory'})
        self.assertIs(guest.cpu, guest.controllers[b'cpu'])
        self.assertIs(guest.cpuacct, guest.controllers[b'cpuacct'])
        self.assertIs(guest.memory, guest.controllers[b'memory'])

    def test_explicit_controller_alias_preserves_legacy_access(self):
        self.mkdir('cpu,cpuacct/guest')
        os.symlink('cpu,cpuacct', os.path.join(self.directory, 'cpu'))
        tree = Tree(self.directory, groups=['cpu'], sub_groups=['guest', 'created'])
        self.assertIsNotNone(tree.get_node_by_path('/cpu/guest'))
        self.assertTrue(os.path.isdir(os.path.join(self.directory, 'cpu,cpuacct', 'created')))
        cpu = tree.get_node_by_path('/cpu')
        cpu.create_cgroup('another')
        cpu.delete_cgroup(b'another')
        grouped = GroupedTree(self.directory)
        self.assertEqual(set(grouped.control_root.controllers), {b'cpu', b'cpuacct'})

    def test_default_tree_keeps_controller_aliases_and_grouped_tree_deduplicates(self):
        self.mkdir('cpu,cpuacct/guest/worker')
        os.symlink('cpu,cpuacct', os.path.join(self.directory, 'cpu'))
        os.symlink('cpu,cpuacct', os.path.join(self.directory, 'cpuacct'))
        tree = Tree(self.directory)
        self.assertIsNotNone(tree.get_node_by_path('/cpu'))
        self.assertIsNotNone(tree.get_node_by_path('/cpu/guest/worker'))
        self.assertIsNotNone(tree.get_node_by_path('/cpuacct/guest'))
        self.assertIsNotNone(tree.get_node_by_path('/cpu,cpuacct/guest'))
        grouped = GroupedTree(self.directory)
        self.assertEqual(len(grouped.control_root.nodes), 1)
        self.assertEqual(len(grouped.control_root.children), 1)
        worker = grouped.get_node_by_raw_path('/guest/worker')
        self.assertEqual(set(worker.controllers), {b'cpu', b'cpuacct'})
        self.assertEqual(len(worker.nodes), 1)

    def test_explicit_alias_order_does_not_duplicate_physical_controllers(self):
        self.mkdir('cpu,cpuacct/guest')
        os.symlink('cpu,cpuacct', os.path.join(self.directory, 'cpu'))
        os.symlink('cpu,cpuacct', os.path.join(self.directory, 'cpuacct'))
        for groups in (['cpu', 'cpuacct', 'cpu,cpuacct'], ['cpu,cpuacct', 'cpuacct', 'cpu']):
            grouped = GroupedTree(self.directory, groups=groups)
            self.assertEqual(len(grouped.control_root.nodes), 1)
            self.assertEqual(set(grouped.get_node_by_path('/guest').controllers), {b'cpu', b'cpuacct'})

    def test_grouped_dedup_preserves_order_of_unrelated_hierarchies(self):
        self.mkdir('unknown/first')
        self.mkdir('cpu/second')
        grouped = GroupedTree(self.directory, groups=['unknown', 'cpu'])
        self.assertEqual([node.name for node in grouped.control_root.nodes], [b'unknown', b'cpu'])

    def test_read_only_tree_also_blocks_controller_writes(self):
        self.mkdir('cpu')
        filename = os.path.join(self.directory, 'cpu', 'cpu.shares')
        with open(filename, 'w') as stream:
            stream.write('1024')
        tree = GroupedTree(self.directory, read_only=True)
        with self.assertRaises(IOError):
            tree.control_root.cpu.shares = 2048
        with open(filename) as stream:
            self.assertEqual(stream.read(), '1024')

    def test_controller_alias_cannot_escape_configured_root(self):
        self.mkdir('outside/mount')
        self.mkdir('root')
        os.symlink('../outside/mount', os.path.join(self.directory, 'root', 'cpu'))
        with self.assertRaises(OSError):
            Tree(os.path.join(self.directory, 'root'), groups=['cpu'])
        self.assertFalse(os.path.exists(os.path.join(self.directory, 'outside', 'mount', 'escape')))

    def test_grouped_collisions_have_explicit_plural_and_raw_lookup(self):
        self.mkdir('cpu/foo.scope')
        self.mkdir('cpu/foo.slice')
        tree = GroupedTree(self.directory)
        matches = tree.get_nodes_by_path('/foo/')
        self.assertEqual(set(node.raw_path for node in matches), {b'/foo.scope', b'/foo.slice'})
        self.assertIs(tree.get_node_by_path('/foo'), matches[0])
        self.assertEqual(tree.get_node_by_raw_path('/foo.scope/').name, b'foo.scope')
        self.assertEqual(tree.get_node_by_raw_path(b'/foo.slice').name, b'foo.slice')
        self.assertIs(tree.get_node_by_raw_path('/'), tree.control_root)

    def test_refresh_replaces_snapshots_and_updates_vm_dictionary(self):
        self.mkdir('cpu/qemu/old')
        tree = VMTree(self.directory)
        old = tree.get_vm_node(b'old')
        self.assertIsNotNone(old)
        shutil.rmtree(os.path.join(self.directory, 'cpu', 'qemu', 'old'))
        self.mkdir('cpu/qemu/new')
        self.assertIs(tree.refresh(), tree)
        self.assertIsNone(tree.get_vm_node('old'))
        self.assertIsNotNone(tree.get_vm_node(b'new'))
        self.assertEqual(old.name, b'old')

    def test_failed_refresh_keeps_previous_snapshot(self):
        self.mkdir('cpu/guest')
        tree = Tree(self.directory)
        old_root = tree.root
        with mock.patch('cgroupspy.trees.os.listdir', side_effect=OSError(errno.EACCES, 'denied')):
            with self.assertRaises(OSError):
                tree.refresh()
        self.assertIs(tree.root, old_root)

    def test_failed_grouped_refresh_keeps_both_previous_snapshots(self):
        self.mkdir('cpu/guest')
        tree = GroupedTree(self.directory)
        old_root = tree.node_tree.root
        old_control_root = tree.control_root
        with mock.patch.object(tree, '_init_control_tree', side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):
                tree.refresh()
        self.assertIs(tree.node_tree.root, old_root)
        self.assertIs(tree.control_root, old_control_root)

    def test_modern_libvirt_layout_preserves_vm_scope_aggregate(self):
        name = r'machine-qemu\x2d1\x2dguest.scope'
        self.mkdir('cpu/machine.slice/' + name + '/libvirt/emulator')
        self.mkdir('cpu/machine.slice/' + name + '/libvirt/vcpu0')
        tree = VMTree(self.directory)
        vm = tree.get_vm_node(to_bytes(name))
        self.assertEqual(vm.raw_path, to_bytes('/machine.slice/' + name))
        self.assertEqual(vm.emulator.raw_path, vm.raw_path + b'/libvirt/emulator')
        self.assertEqual([node.name for node in vm.vcpus], [b'vcpu0'])
