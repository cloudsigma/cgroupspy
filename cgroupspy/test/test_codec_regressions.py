"""Regressions for v1 control-file codecs and kernel write commands."""
import errno
import os
import shutil
import tempfile
from collections import namedtuple
from unittest import TestCase

try:
    from unittest import mock
except ImportError:
    import mock

from ..contenttypes import DeviceAccess, DeviceThrottle
from ..controllers import (BlkIOController, Controller, CpuController,
                           CpuSetController, DevicesController, MemoryController,
                           NetPrioController)
from ..interfaces import (BaseFileInterface, BatchWriteError, CommaDashSetFile,
                          DictFile, DictOrFlagFile, IntegerFile, IntegerListFile,
                          ListFile, MultiLineIntegerFile, OomControlFile,
                          SplitValueFile, TypedFile)


class CodecRegressionTests(TestCase):
    def test_device_access_zero_is_not_all_permissions(self):
        rule = DeviceAccess('c', 1, 3, 0)
        self.assertFalse(rule.can_read)
        self.assertFalse(rule.can_write)
        self.assertFalse(rule.can_mknod)
        self.assertRaises(ValueError, str, rule)
        self.assertEqual(str(DeviceAccess('c', 1, 3)), 'c 1:3 rwm')

    def test_device_access_rejects_unknown_rights_and_masks(self):
        for value in ('c 1:3 xyz', 'c 1:3 rwx', 'x 1:3 r'):
            self.assertRaises(ValueError, DeviceAccess.from_string, value)
        for access in (-1, 8, 1.5):
            self.assertRaises(ValueError, DeviceAccess, 'c', 1, 3, access)

    def test_device_access_preserves_valid_rights_and_wildcards(self):
        for value in ('c 1:3 rwm', 'b *:* rw', 'a *:* rwm', 'all *:* rwm'):
            self.assertEqual(str(DeviceAccess.from_string(value)), value)
        self.assertEqual(str(DeviceAccess('b', '*', '*', 'm')), 'b *:* m')

    def test_all_device_command_cannot_imply_narrower_permissions(self):
        for rule in (DeviceAccess('a', 1, 3, 'rwm'), DeviceAccess('all', access='r')):
            self.assertRaises(ValueError, str, rule)

    def test_contenttype_equality_handles_other_objects(self):
        self.assertFalse(DeviceAccess('c', 1, 3) == object())
        self.assertFalse(DeviceThrottle(10, 8, 0) == object())

    def test_device_throttle_rejects_invalid_numeric_fields(self):
        for value in (-1, 1.5, '1\n8:16 2'):
            self.assertRaises(ValueError, DeviceThrottle, value, 8, 0)
        self.assertEqual(str(DeviceThrottle('100', 8, 0)), '8:0 100')
        self.assertEqual(str(DeviceThrottle(0, 8, 0)), '8:0 0')

    def test_empty_and_blank_dictionary_rows(self):
        field = DictFile('stats')
        self.assertEqual(field.sanitize_get(''), {})
        self.assertEqual(field.sanitize_get('\n a 1\n\n b 2\n'), {'a': 1, 'b': 2})

    def test_dictionary_commands_reject_injected_keys_and_values(self):
        field = DictFile('map')
        self.assertRaises(ValueError, field.sanitize_set, {'eth0\neth1': 2})
        self.assertRaises(ValueError, field.sanitize_set, {'eth0': '1\neth1 2'})
        self.assertRaises(ValueError, field.sanitize_set, {'eth0': 1.5})

    def test_cpuset_string_and_generator_inputs(self):
        field = CommaDashSetFile('cpuset.cpus')
        self.assertEqual(field.sanitize_set('1-3,5'), '1-3,5')
        self.assertEqual(field.sanitize_set(b'1-3,5'), '1-3,5')
        self.assertEqual(field.sanitize_set(iter([3, 2, 1, 5])), '1-3,5')
        for value in ('', ' ', [], set(), {}):
            self.assertEqual(field.sanitize_set(value), ' ')
        self.assertRaises(ValueError, field.sanitize_set, [-1])
        self.assertRaises(ValueError, field.sanitize_set, '3-1')

    def test_integer_list_none_is_one_sentinel(self):
        self.assertEqual(IntegerListFile('list').sanitize_set(None), '-1')
        self.assertEqual(IntegerFile('limit').sanitize_get('-1'), None)
        self.assertEqual(IntegerFile('limit').sanitize_set(None), -1)

    def test_descriptor_modes_support_explicit_false_and_class_access(self):
        self.assertFalse(ListFile('list', readonly=False).readonly)
        self.assertRaises(RuntimeError, ListFile, 'list', writeonly=True)
        self.assertIsInstance(CpuController.shares, IntegerFile)
        self.assertIsInstance(DevicesController.allow, TypedFile)

    def test_typed_many_accepts_text_and_validates_every_entry(self):
        field = TypedFile('limits', DeviceThrottle, many=True)
        value = '8:0 100\n8:16 200'
        self.assertEqual(field.sanitize_set(value), value)
        self.assertEqual(field.sanitize_set(value.encode('ascii')), value)
        self.assertRaises(RuntimeError, field.sanitize_set, [object()])

    def test_oom_control_preserves_status_and_writes_flag_only(self):
        field = OomControlFile('memory.oom_control')
        self.assertEqual(field.sanitize_get('oom_kill_disable 1\nunder_oom 0'),
                         {'oom_kill_disable': 1, 'under_oom': 0})
        self.assertEqual(field.sanitize_set({'oom_kill_disable': True}), 1)
        self.assertEqual(field.sanitize_set(False), 0)
        self.assertEqual(field.sanitize_set(2), 1)
        self.assertRaises(ValueError, field.sanitize_set, {'under_oom': 0})
        self.assertRaises(ValueError, field.sanitize_set, [1])
        self.assertRaises(ValueError, DictOrFlagFile('flag').sanitize_set, [1])

    def test_block_io_statistics_choose_standalone_aggregate(self):
        value = ('8:0 Read 1024\n8:0 Write 2048\n8:0 Total 3072\n'
                 '8:16 Read 4096\n8:16 Total 4096\n Total   7168\n')
        field = SplitValueFile('blkio.io_service_bytes', 1, int)
        self.assertEqual(field.sanitize_get(value), 7168)
        self.assertEqual(field.sanitize_get('Total 0'), 0)
        self.assertRaises(ValueError, field.sanitize_get, '8:0 Read 1\n8:0 Total 1')
        self.assertRaises(ValueError, field.sanitize_get, '')

    def test_generic_single_line_split_interface_is_preserved(self):
        field = SplitValueFile('value', 1, int)
        self.assertEqual(field.sanitize_get('Other 12'), 12)
        field = SplitValueFile('value', 1, int, splitchar=':')
        self.assertEqual(field.sanitize_get('Other:12'), 12)


class ControllerRegressionTests(TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)
        self.node = namedtuple('Node', 'full_path')(self.directory.encode('utf-8'))

    def write_fixture(self, filename, value):
        with open(os.path.join(self.directory, filename), 'w') as stream:
            stream.write(value)

    def test_throttle_write_paths_use_real_filenames(self):
        controller = BlkIOController(self.node)
        for name in ('bps', 'iops'):
            self.write_fixture('blkio.throttle.write_{}_device'.format(name), '8:0 100')
            value = getattr(controller, 'throttle_write_{}_device'.format(name))
            self.assertEqual(value, [DeviceThrottle(100, 8, 0)])

    def test_numeric_pressure_alias_retains_boolean_property(self):
        self.write_fixture('cpuset.memory_pressure', '2418')
        controller = CpuSetController(self.node)
        self.assertIs(controller.memory_pressure, True)
        self.assertEqual(controller.memory_pressure_rate, 2418)
        self.assertRaises(RuntimeError, setattr, controller, 'memory_pressure_rate', 1)

    def test_inherited_and_overridden_interfaces_follow_mro(self):
        class CustomCpu(CpuController):
            tasks = MultiLineIntegerFile('custom.tasks')

        class LeafCpu(CustomCpu):
            notify_on_release = None

        controller = LeafCpu(self.node)
        interfaces = controller.list_interfaces()
        self.assertIs(controller.get_interface('shares'), CpuController.shares)
        self.assertIs(interfaces['tasks'], CustomCpu.tasks)
        self.assertNotIn('notify_on_release', interfaces)
        self.assertIsNone(controller.get_interface('notify_on_release'))

    def test_descriptor_paths_support_text_and_bytes_roots(self):
        self.write_fixture('cpu.shares', '1024')
        text_node = namedtuple('Node', 'full_path')(self.directory)
        self.assertEqual(CpuController(text_node).shares, 1024)
        self.assertEqual(CpuController(self.node).shares, 1024)
        self.assertIsInstance(Controller(self.node).filepath('tasks'), bytes)

    def test_get_content_strict_mode_preserves_default_missing_result(self):
        controller = CpuController(self.node)
        self.assertIsNone(controller.get_content('shares'))
        self.assertRaises(IOError, controller.get_content, 'shares', strict=True)
        self.assertIsNone(controller.get_content('unknown', strict=True))

    def test_each_pid_and_device_rule_uses_a_separate_open(self):
        file_mock = mock.mock_open()
        with mock.patch('cgroupspy.controllers.open', file_mock, create=True):
            controller = Controller(self.node)
            controller.tasks = [100, 101]
            self.assertEqual(file_mock.call_count, 2)
            self.assertEqual(file_mock().write.call_args_list,
                             [mock.call('100'), mock.call('101')])

        file_mock = mock.mock_open()
        with mock.patch('cgroupspy.controllers.open', file_mock, create=True):
            controller = BlkIOController(self.node)
            controller.throttle_read_bps_device = [DeviceThrottle(100, 8, 0),
                                                   DeviceThrottle(200, 8, 16)]
            self.assertEqual(file_mock.call_count, 2)
            self.assertEqual(file_mock().write.call_args_list,
                             [mock.call('8:0 100'), mock.call('8:16 200')])

    def test_net_priority_dictionary_uses_one_command_per_interface(self):
        controller = NetPrioController(self.node)
        with mock.patch.object(controller, 'set_property') as setter:
            controller.ifpriomap = {'eth1': 2, 'eth0': 1}
            self.assertEqual(setter.call_args_list,
                             [mock.call(b'net_prio.ifpriomap', 'eth0 1'),
                              mock.call(b'net_prio.ifpriomap', 'eth1 2')])

    def test_complete_batches_validate_before_first_write(self):
        controller = Controller(self.node)
        with mock.patch.object(controller, 'set_property') as setter:
            self.assertRaises(ValueError, setattr, controller, 'tasks', [100, 'bad'])
            self.assertRaises(ValueError, setattr, controller, 'tasks', [100, 1.5])
            self.assertEqual(setter.call_count, 0)
        controller = BlkIOController(self.node)
        with mock.patch.object(controller, 'set_property') as setter:
            self.assertRaises(RuntimeError, setattr, controller,
                              'throttle_read_bps_device', ['8:0 100', 'bad'])
            mutated = DeviceThrottle(100, 8, 0)
            mutated.limit = '100\n8:16 bad'
            self.assertRaises(RuntimeError, setattr, controller,
                              'throttle_read_bps_device', [DeviceThrottle(100, 8, 0), mutated])
            self.assertEqual(setter.call_count, 0)

    def test_partial_failure_preserves_errno_and_reports_completed_records(self):
        controller = Controller(self.node)
        original = IOError(errno.ESRCH, 'No such process')
        with mock.patch.object(controller, 'set_property', side_effect=[3, original]) as setter:
            try:
                controller.tasks = [100, 101, 102]
            except BatchWriteError as error:
                self.assertEqual(error.errno, errno.ESRCH)
                self.assertEqual(error.completed_records, ('100',))
                self.assertEqual(error.failed_record, '101')
                self.assertEqual(error.record_index, 1)
                self.assertIs(error.original_error, original)
            else:
                self.fail('Expected a partial batch failure')
            self.assertEqual(setter.call_count, 2)

    def test_device_allow_malformed_rule_does_not_write(self):
        controller = DevicesController(self.node)
        with mock.patch.object(controller, 'set_property') as setter:
            self.assertRaises(ValueError, setattr, controller, 'allow', 'c 1:3 xyz')
            self.assertRaises(ValueError, setattr, controller, 'allow', DeviceAccess('c', 1, 3, 0))
            self.assertEqual(setter.call_count, 0)

    def test_oom_dictionary_assignment_sends_only_disable_flag(self):
        controller = MemoryController(self.node)
        with mock.patch.object(controller, 'set_property') as setter:
            controller.oom_control = {'oom_kill_disable': True}
            setter.assert_called_once_with(b'memory.oom_control', 1)

    def test_read_only_node_blocks_direct_and_descriptor_writes(self):
        node = namedtuple('ReadOnlyNode', 'full_path read_only')(self.node.full_path, True)
        controller = CpuController(node)
        file_mock = mock.mock_open()
        with mock.patch('cgroupspy.controllers.open', file_mock, create=True):
            self.assertRaises(IOError, controller.set_property, 'cpu.shares', 1024)
            self.assertRaises(IOError, setattr, controller, 'shares', 1024)
            self.assertRaises(BatchWriteError, setattr, controller, 'tasks', [100])
            self.assertEqual(file_mock.call_count, 0)

    def test_controller_write_rejects_replaced_subgroup_symlink(self):
        from ..nodes import Node
        cpu_path = os.path.join(self.directory, 'cpu')
        target_path = os.path.join(self.directory, 'target')
        os.mkdir(cpu_path)
        os.mkdir(target_path)
        with open(os.path.join(target_path, 'cpu.shares'), 'w') as stream:
            stream.write('1024')
        root = Node(self.directory)
        cpu = Node('cpu', parent=root)
        guest = Node('guest', parent=cpu)
        os.symlink(target_path, os.path.join(cpu_path, 'guest'))
        self.assertRaises(OSError, setattr, guest.controller, 'shares', 2048)
        with open(os.path.join(target_path, 'cpu.shares')) as stream:
            self.assertEqual(stream.read(), '1024')
