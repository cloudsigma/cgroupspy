"""Codec and API contract tests; regular files cannot emulate kernel enforcement."""
from __future__ import absolute_import

import errno
import os
import shutil
import tempfile
import unittest

try:
    import unittest.mock as mock
except ImportError:
    import mock

from cgroupspy import v2


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root_path = os.path.join(self.tmp, 'cgroup')
        os.mkdir(self.root_path)
        self.write('cgroup.controllers', 'cpu memory cpuset pids io\n')
        self.write('cgroup.subtree_control', 'cpu memory\n')
        self.write('cgroup.procs', '42\n7\n42\n')
        self.write('cgroup.threads', '43\n44\n')
        self.write('cgroup.type', 'domain\n')
        self.write('cgroup.events', 'populated 1\nfrozen 0\nfuture 2\n')
        self.write('cgroup.stat', 'nr_descendants 3\nnr_dying_descendants 0\n')
        self.write('cgroup.freeze', '0\n')
        self.trees = []

    def tearDown(self):
        for tree in self.trees:
            tree.close()
        shutil.rmtree(self.tmp)

    def write(self, filename, data):
        with open(os.path.join(self.root_path, filename), 'wb') as target:
            target.write(data.encode('ascii'))

    def read(self, filename):
        with open(os.path.join(self.root_path, filename), 'rb') as source:
            return source.read().decode('ascii')

    def tree(self, writable=False):
        tree = v2.V2Tree(self.root_path, writable=writable, delegated=writable)
        self.trees.append(tree)
        return tree

    def test_native_membership_and_capabilities(self):
        node = self.tree().root
        self.assertEqual(node.procs, set([7, 42]))
        self.assertEqual(node.threads, set([43, 44]))
        self.assertEqual(node.type, 'domain')
        self.assertEqual(node.events, {'populated': 1, 'frozen': 0, 'future': 2})
        self.assertEqual(node.stat['nr_descendants'], 3)
        self.assertFalse(node.frozen)
        caps = node.capabilities
        self.assertEqual(caps['available_controllers'], frozenset(['cpu', 'memory', 'cpuset', 'pids', 'io']))
        self.assertEqual(caps['enabled_controllers'], frozenset(['cpu', 'memory']))
        self.assertIn('cgroup.procs', caps['files'])
        self.assertFalse(caps['write_enabled'])
        self.assertFalse(node.has_file('cpu.max'))

    def test_readonly_default_does_not_mutate(self):
        node = self.tree().root
        with self.assertRaises(IOError):
            node.attach_process(42)
        with self.assertRaises(IOError):
            node.create_cgroup('new')
        with self.assertRaises(IOError):
            node.delete_cgroup('existing')
        self.assertEqual(self.read('cgroup.procs'), '42\n7\n42\n')
        with self.assertRaises(ValueError):
            v2.V2Tree(self.root_path, writable=True)

    def test_root_marker_required_and_close(self):
        os.mkdir(os.path.join(self.tmp, 'ordinary'))
        with self.assertRaises(IOError):
            v2.V2Tree(os.path.join(self.tmp, 'ordinary'))
        tree = self.tree()
        tree.close()
        tree.close()
        with self.assertRaises(ValueError):
            tree.root.procs
        with v2.V2Tree(self.root_path) as other:
            self.assertEqual(other.root.path, '/')
        with self.assertRaises(ValueError):
            other.root.procs

    def test_root_descriptor_closes_when_cloexec_setup_fails(self):
        descriptors = []

        def fail_cloexec(descriptor):
            descriptors.append(descriptor)
            raise OSError(errno.EIO, 'Cannot configure descriptor flags')

        with mock.patch.object(v2, '_set_cloexec', side_effect=fail_cloexec):
            with self.assertRaises(OSError):
                v2.V2Tree(self.root_path)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptors[0])
        self.assertEqual(caught.exception.errno, errno.EBADF)

    def test_open_at_closes_when_cloexec_setup_fails(self):
        for native_openat in (False, True):
            descriptor = os.open(self.root_path, os.O_RDONLY)
            try:
                failure = OSError(errno.EIO, 'Flag failure')
                with mock.patch.object(v2.os, 'open', return_value=descriptor) as open_file:
                    supported = set([open_file]) if native_openat else set()
                    with mock.patch.object(v2.os, 'supports_dir_fd', supported, create=True):
                        with mock.patch.object(v2, '_set_cloexec', side_effect=failure):
                            with self.assertRaises(OSError) as caught:
                                unexpected_descriptor = v2._open_at(123, 'cgroup.procs', os.O_RDONLY)
                                os.close(unexpected_descriptor)
                self.assertIs(caught.exception, failure)
                with self.assertRaises(OSError) as caught:
                    os.fstat(descriptor)
                self.assertEqual(caught.exception.errno, errno.EBADF)
            finally:
                try:
                    os.close(descriptor)
                except OSError as error:
                    # The expected failure closes it; still clean up if a regression leaks it.
                    if error.errno != errno.EBADF:
                        raise

    def test_directory_duplicate_closes_when_cloexec_setup_fails(self):
        tree = self.tree()
        descriptors = []
        failure = OSError(errno.EIO, 'Cannot configure descriptor flags')

        def fail_cloexec(descriptor):
            descriptors.append(descriptor)
            raise failure

        with mock.patch.object(v2, '_set_cloexec', side_effect=fail_cloexec):
            with self.assertRaises(OSError) as caught:
                unexpected_descriptor = tree._directory(())
                os.close(unexpected_descriptor)
        self.assertIs(caught.exception, failure)
        self.assertEqual(len(descriptors), 1)
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptors[0])
        self.assertEqual(caught.exception.errno, errno.EBADF)
        self.assertEqual(tree.root.procs, set([7, 42]))

    def test_tree_children_are_live_and_paths_are_native_text(self):
        tree = self.tree()
        os.mkdir(os.path.join(self.root_path, 'a'))
        os.mkdir(os.path.join(self.root_path, 'a', 'b'))
        self.assertEqual([node.path for node in tree.walk()], ['/', '/a', '/a/b'])
        self.assertEqual(tree.get_node_by_path(b'/a/b/').parent.path, '/a')
        self.assertEqual(tree.get_node_by_path('/a').full_path, os.path.join(os.path.realpath(self.root_path), 'a'))
        self.assertIsNone(tree.get_node_by_path('missing'))
        self.assertIsNone(tree.root.parent)
        os.mkdir(os.path.join(self.root_path, 'c'))
        self.assertEqual([node.name for node in tree.root.children], ['a', 'c'])

    def test_paths_refuse_traversal_and_symlinks(self):
        tree = self.tree(True)
        for path in ('..', '/a/../b', '/a//b', '//a', '/a/./b', '/a\x00b'):
            with self.assertRaises(ValueError):
                tree.get_node_by_path(path)
        for name in ('../escape', '/escape', '.', '..', 'a/b', 'a\nb'):
            with self.assertRaises(ValueError):
                tree.root.create_cgroup(name)
        for filename in ('../cgroup.procs', '/cgroup.procs', 'a/b', 'cgroup.procs\n'):
            with self.assertRaises(ValueError):
                tree.root.read_raw(filename)
        os.mkdir(os.path.join(self.tmp, 'outside'))
        os.symlink(os.path.join(self.tmp, 'outside'), os.path.join(self.root_path, 'alias'))
        self.assertEqual(tree.root.children, [])
        with self.assertRaises(OSError):
            tree.get_node_by_path('/alias')
        self.write('outside-data', '42\n')
        os.symlink(os.path.join(self.root_path, 'outside-data'), os.path.join(self.root_path, 'linked'))
        self.assertFalse(tree.root.has_file('linked'))
        with self.assertRaises((IOError, OSError)):
            tree.root.read_raw('linked')
        with self.assertRaises(v2.BatchWriteError):
            tree.root.write_records('linked', ['99'])
        self.assertEqual(self.read('outside-data'), '42\n')

    def test_operations_remain_anchored_after_root_path_replacement(self):
        tree = self.tree(True)
        original = self.root_path + '-original'
        os.rename(self.root_path, original)
        os.mkdir(self.root_path)
        self.write('cgroup.procs', '999\n')
        self.assertEqual(tree.root.procs, set([7, 42]))
        tree.root.attach_process(77)
        self.assertEqual(self.read('cgroup.procs'), '999\n')
        with open(os.path.join(original, 'cgroup.procs'), 'rb') as source:
            self.assertEqual(source.read(), b'77\n')

    def test_explicit_create_delete_and_no_implicit_file_creation(self):
        node = self.tree(True).root
        child = node.create_cgroup('new')
        self.assertEqual(child.path, '/new')
        self.assertTrue(os.path.isdir(child.full_path))
        node.delete_cgroup('new')
        self.assertFalse(os.path.exists(child.full_path))
        with self.assertRaises(v2.BatchWriteError):
            node.write_records('unknown.future', ['1'])
        self.assertFalse(os.path.exists(os.path.join(self.root_path, 'unknown.future')))

    def test_membership_writes_are_one_syscall_per_record(self):
        node = self.tree(True).root
        calls = []
        original_write = v2.os.write

        def record_write(descriptor, data):
            calls.append(data)
            return original_write(descriptor, data)

        v2.os.write = record_write
        try:
            node.attach_processes([10, 11])
            node.attach_threads([12, 13])
        finally:
            v2.os.write = original_write
        self.assertEqual(calls, [b'10\n', b'11\n', b'12\n', b'13\n'])
        self.assertEqual(self.read('cgroup.procs'), '11\n')
        self.assertEqual(self.read('cgroup.threads'), '13\n')

    def test_writes_clear_nonblocking_flag_before_kernel_commands(self):
        self.write('memory.high', '8192\n')
        self.write('memory.max', '16384\n')
        memory = self.tree(True).root.memory
        original_write = v2.os.write
        observed_flags = []

        def check_write(descriptor, data):
            observed_flags.append(v2.fcntl.fcntl(descriptor, v2.fcntl.F_GETFL))
            return original_write(descriptor, data)

        v2.os.write = check_write
        try:
            memory.high = 4096
            memory.max = 8192
        finally:
            v2.os.write = original_write
        self.assertEqual(len(observed_flags), 2)
        self.assertTrue(all(not flags & os.O_NONBLOCK for flags in observed_flags))
        self.assertEqual(self.read('memory.high'), '4096\n')
        self.assertEqual(self.read('memory.max'), '8192\n')

    def test_all_batch_inputs_are_validated_before_writing(self):
        node = self.tree(True).root
        for values in ([10, -1], [10, True], [10, '12']):
            with self.assertRaises((TypeError, ValueError)):
                node.attach_processes(values)
        for records in (['10', '11\n12'], ['10', 'bad\x00'], ['10', u'\u00e9']):
            with self.assertRaises((ValueError, UnicodeError)):
                node.write_records('cgroup.procs', records)
        with self.assertRaises(TypeError):
            node.write_records('cgroup.procs', '10')
        self.assertEqual(self.read('cgroup.procs'), '42\n7\n42\n')

    def test_partial_batch_failure_exposes_progress_and_original_errno(self):
        node = self.tree(True).root
        original_write = v2.os.write
        calls = []

        def fail_second(descriptor, data):
            calls.append(data)
            if len(calls) == 2:
                raise OSError(errno.ESRCH, 'Process has exited')
            return original_write(descriptor, data)

        v2.os.write = fail_second
        try:
            with self.assertRaises(v2.BatchWriteError) as caught:
                node.attach_processes([10, 11, 12])
        finally:
            v2.os.write = original_write
        self.assertEqual(caught.exception.completed, ('10',))
        self.assertEqual(caught.exception.failed_record, '11')
        self.assertEqual(caught.exception.original_error.errno, errno.ESRCH)
        self.assertEqual(caught.exception.errno, errno.ESRCH)
        self.assertEqual(calls, [b'10\n', b'11\n'])

    def test_short_write_is_not_retried(self):
        original_write = v2.os.write
        v2.os.write = lambda descriptor, data: len(data) - 1
        try:
            with self.assertRaises(v2.BatchWriteError) as caught:
                self.tree(True).root.attach_process(10)
        finally:
            v2.os.write = original_write
        self.assertEqual(caught.exception.completed, ())
        self.assertEqual(caught.exception.errno, errno.EIO)

    def test_controller_enablement_is_explicit_and_validated(self):
        node = self.tree(True).root
        self.assertEqual(self.read('cgroup.subtree_control'), 'cpu memory\n')
        node.enable_controllers(['cpu', 'pids'])
        self.assertEqual(self.read('cgroup.subtree_control'), '+cpu +pids\n')
        with self.assertRaises(ValueError):
            node.enable_controllers(['cpu', 'unavailable'])
        with self.assertRaises(TypeError):
            node.enable_controllers('cpu')
        self.assertEqual(self.read('cgroup.subtree_control'), '+cpu +pids\n')
        self.assertEqual(self.read('cgroup.procs'), '42\n7\n42\n')
        node.disable_controllers(['pids'])
        self.assertEqual(self.read('cgroup.subtree_control'), '-pids\n')

    def test_cpu_native_units_quota_pair_and_unlimited(self):
        self.write('cpu.max', 'max 100000\n')
        self.write('cpu.weight', '100\n')
        self.write('cpu.stat', 'usage_usec 123\nuser_usec 80\nsystem_usec 43\nnew_stat 7\n')
        cpu = self.tree(True).root.cpu
        self.assertEqual(cpu.max, v2.CpuMax(None, 100000))
        self.assertEqual(cpu.stat['usage_usec'], 123)
        self.assertEqual(cpu.stat['new_stat'], 7)
        cpu.max = (20000, 100000)
        self.assertEqual(self.read('cpu.max'), '20000 100000\n')
        cpu.max = (None, 200000)
        self.assertEqual(cpu.max, v2.CpuMax(None, 200000))
        cpu.weight = 200
        self.assertEqual(cpu.weight, 200)
        for value in (0, 10001, -1):
            with self.assertRaises(ValueError):
                cpu.weight = value
        with self.assertRaises(ValueError):
            cpu.max = (1, 1)

    def test_memory_byte_limits_and_events_preserve_unknown_keys(self):
        for name, data in [('memory.current', '4096'), ('memory.max', 'max'),
                           ('memory.high', '8192'), ('memory.low', '0'),
                           ('memory.min', '0'), ('memory.swap.current', '2048'),
                           ('memory.swap.max', '4096'), ('memory.stat', 'anon 2048\nfuture_key 4'),
                           ('memory.events', 'oom 1\noom_kill 0'),
                           ('memory.events.local', 'oom 0')]:
            self.write(name, data + '\n')
        memory = self.tree(True).root.memory
        self.assertEqual(memory.current, 4096)
        self.assertIsNone(memory.max)
        self.assertEqual(memory.swap_max, 4096)
        self.assertEqual(memory.stat['future_key'], 4)
        self.assertEqual(memory.events['oom'], 1)
        self.assertEqual(memory.events_local['oom'], 0)
        memory.max = 12288
        memory.swap_max = None
        memory.high = None
        memory.low = 4096
        self.assertEqual(self.read('memory.max'), '12288\n')
        self.assertIsNone(memory.swap_max)
        self.assertIsNone(memory.high)
        self.assertEqual(memory.low, 4096)
        with self.assertRaises(ValueError):
            memory.max = -1

    def test_cpuset_ranges_empty_inheritance_and_effective_masks(self):
        self.write('cpuset.cpus', '\n')
        self.write('cpuset.cpus.effective', '0-3,5\n')
        self.write('cpuset.mems', '0\n')
        self.write('cpuset.mems.effective', '0-1\n')
        cpuset = self.tree(True).root.cpuset
        self.assertEqual(cpuset.cpus, set())
        self.assertEqual(cpuset.cpus_effective, set([0, 1, 2, 3, 5]))
        self.assertEqual(cpuset.mems_effective, set([0, 1]))
        cpuset.cpus = [4, 1, 2, 3, 7, 7]
        self.assertEqual(self.read('cpuset.cpus'), '1-4,7\n')
        cpuset.cpus = []
        self.assertEqual(self.read('cpuset.cpus'), '\n')
        with self.assertRaises(TypeError):
            cpuset.cpus = [1, True]

    def test_pids_limits_and_optional_files_fail_explicitly(self):
        self.write('pids.current', '3\n')
        self.write('pids.max', 'max\n')
        self.write('pids.events', 'max 2\n')
        pids = self.tree(True).root.pids
        self.assertEqual(pids.current, 3)
        self.assertIsNone(pids.max)
        self.assertEqual(pids.events, {'max': 2})
        pids.max = 0
        self.assertEqual(pids.max, 0)
        with self.assertRaises(IOError):
            pids.peak

    def test_io_keyed_values_native_units_and_updates(self):
        self.write('io.stat', '8:0 rbytes=4096 wbytes=1024 rios=2 wios=1 future=7\n')
        self.write('io.max', '8:0 rbps=max wbps=8192 riops=max wiops=10\n')
        self.write('io.weight', 'default 100\n8:0 200\n')
        io = self.tree(True).root.io
        self.assertEqual(io.stat['8:0']['rbytes'], 4096)
        self.assertEqual(io.stat['8:0']['future'], 7)
        self.assertIsNone(io.max['8:0']['rbps'])
        self.assertEqual(io.max['8:0']['wbps'], 8192)
        self.assertEqual(io.weight, {'default': 100, '8:0': 200})
        io.set_max('8:0', rbps=4096, wiops=None)
        self.assertEqual(self.read('io.max'), '8:0 rbps=4096 wiops=max\n')
        io.set_weight(300)
        self.assertEqual(self.read('io.weight'), 'default 300\n')
        io.set_weight(400, device='8:1')
        self.assertEqual(self.read('io.weight'), '8:1 400\n')
        for device in ('/dev/sda', '8:0\n', '8:-1'):
            with self.assertRaises(ValueError):
                io.set_max(device, rbps=1)
        with self.assertRaises(ValueError):
            io.set_max('8:0', unknown=1)

    def test_pressure_averages_are_float_totals_are_microseconds(self):
        value = 'some avg10=0.10 avg60=1.20 avg300=2.30 total=12345 future=7\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n'
        for name in ('cpu.pressure', 'memory.pressure', 'io.pressure'):
            self.write(name, value)
        node = self.tree().root
        for pressure in (node.cpu.pressure, node.memory.pressure, node.io.pressure):
            self.assertEqual(pressure['some']['avg10'], 0.1)
            self.assertIsInstance(pressure['some']['avg10'], float)
            self.assertEqual(pressure['some']['total'], 12345)
            self.assertEqual(pressure['some']['future'], 7)

    def test_freezing_does_not_claim_synchronous_completion(self):
        node = self.tree(True).root
        node.frozen = True
        self.assertTrue(node.frozen)
        self.assertEqual(node.events['frozen'], 0)
        with self.assertRaises(TypeError):
            node.frozen = 1


if __name__ == '__main__':
    unittest.main()
