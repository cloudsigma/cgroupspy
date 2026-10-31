"""Explicit, native cgroup v2 access without changing the legacy v1 API.

Values retain kernel units: byte counts are bytes and ``*_usec`` statistics
are microseconds.  ``None`` represents the kernel's ``max`` sentinel.  Reading
never enables controllers or migrates processes.  Writing requires both
``writable=True`` and ``delegated=True`` for an explicitly selected root.
The latter is a caller assertion of ownership, not a privilege grant.
"""
from __future__ import absolute_import

import errno
import fcntl
import os
import re
import stat
import sys
from collections import namedtuple

try:
    text_type = unicode
    integer_types = (int, long)
except NameError:
    text_type = str
    integer_types = (int,)


CpuMax = namedtuple('CpuMax', 'quota_us period_us')
_DEVICE = re.compile(r'^[0-9]+:[0-9]+\Z')
_FILENAME = re.compile(r'^[a-zA-Z0-9_.]+\Z')
_INTEGER = re.compile(r'^-?[0-9]+\Z')


def _text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, bytes):
        if hasattr(os, 'fsdecode'):
            return os.fsdecode(value)
        return value.decode(sys.getfilesystemencoding() or 'utf-8')
    raise TypeError('Expected a string or bytes')


def _name(value):
    value = _text(value)
    if not value or value in ('.', '..') or any(c in value for c in '/\x00\n\r'):
        raise ValueError('Expected a single cgroup name')
    return value


def _filename(value):
    value = _text(value)
    if not _FILENAME.match(value) or value in ('.', '..'):
        raise ValueError('Invalid cgroup interface filename')
    return value


def _parts(path):
    path = _text(path)
    if path in ('', '/'):
        return ()
    path = path[1:] if path.startswith('/') else path
    path = path[:-1] if path.endswith('/') else path
    return tuple(_name(part) for part in path.split('/'))


def _number(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, integer_types):
        raise TypeError('Expected an integer')
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError('Integer outside the supported range')
    return str(value)


def _limit(value):
    return 'max' if value is None else _number(value)


def _parse_limit(value):
    value = value.strip()
    return None if value == 'max' else int(value)


def _scalar(value):
    if value == 'max':
        return None
    if _INTEGER.match(value):
        return int(value)
    return value


def _flat(value):
    result = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        key, data = line.split(None, 1)
        result[key] = _scalar(data.strip())
    return result


def _keyed(value):
    result = {}
    for line in value.splitlines():
        fields = line.split()
        if not fields:
            continue
        values = {}
        for field in fields[1:]:
            key, data = field.split('=', 1)
            values[key] = _scalar(data)
        result[fields[0]] = values
    return result


def _pressure(value):
    result = _keyed(value)
    for values in result.values():
        for key in ('avg10', 'avg60', 'avg300'):
            if key in values:
                values[key] = float(values[key])
    return result


def _cpus(value):
    result = set()
    for item in value.strip().split(','):
        if not item:
            continue
        bounds = item.split('-')
        if len(bounds) == 1:
            start = end = int(bounds[0])
        elif len(bounds) == 2:
            start, end = map(int, bounds)
        else:
            raise ValueError('Invalid CPU/node range')
        if start < 0 or end < start:
            raise ValueError('Invalid CPU/node range')
        result.update(range(start, end + 1))
    return result


def _format_cpus(values):
    values = list(values)
    for value in values:
        _number(value)
    values = sorted(set(values))
    result = []
    if not values:
        return ''
    first = last = values[0]
    for value in values[1:]:
        if value == last + 1:
            last = value
            continue
        result.append(str(first) if first == last else '{}-{}'.format(first, last))
        first = last = value
    result.append(str(first) if first == last else '{}-{}'.format(first, last))
    return ','.join(result)


def _device(value):
    value = _text(value)
    if not _DEVICE.match(value):
        raise ValueError('Expected a major:minor device number')
    return value


def _set_cloexec(descriptor):
    """Set descriptor flags; the acquisition scope retains ownership."""
    if hasattr(os, 'set_inheritable'):
        os.set_inheritable(descriptor, False)
    else:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
        fcntl.fcntl(descriptor, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)


def _open_at(directory, name, flags):
    """Use openat, or Linux procfs for the retained Python 2.7 runtime."""
    flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    if os.open in getattr(os, 'supports_dir_fd', ()):
        descriptor = os.open(name, flags, dir_fd=directory)
    else:
        descriptor = os.open('/proc/self/fd/{}/{}'.format(directory, name), flags)
    try:
        _set_cloexec(descriptor)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _stat_at(directory, name):
    if os.stat in getattr(os, 'supports_dir_fd', ()):
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    return os.lstat('/proc/self/fd/{}/{}'.format(directory, name))


def _listdir(directory):
    if os.listdir in getattr(os, 'supports_fd', ()):
        return os.listdir(directory)
    return os.listdir('/proc/self/fd/{}'.format(directory))


class BatchWriteError(IOError):
    """A command failed; earlier records remain applied (there is no rollback).

    ``completed`` contains records accepted before ``failed_record``;
    ``original_error`` preserves the kernel error.  The failed record's effects
    can be indeterminate after a short write or other I/O failure.
    """

    def __init__(self, path, completed, failed_record, original_error):
        self.completed = tuple(completed)
        self.failed_record = failed_record
        self.original_error = original_error
        message = 'cgroup write failed after {} accepted record(s): {}'.format(
            len(completed), original_error)
        IOError.__init__(self, getattr(original_error, 'errno', errno.EIO), message, path)


class V2Tree(object):
    """A live view of one namespace-visible cgroup v2 subtree.

    The root must contain ``cgroup.controllers``.  Directory descriptors anchor
    operations beneath this root; descendant symlinks and traversal components
    are refused.  Use as a context manager or call ``close()`` when finished.
    """

    def __init__(self, root_path, writable=False, delegated=False):
        if writable and not delegated:
            raise ValueError('Writing requires an explicitly delegated root')
        self.root_path = os.path.realpath(os.path.abspath(_text(root_path)))
        self.writable = bool(writable)
        self.delegated = bool(delegated)
        self._root_fd = None
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        self._root_fd = os.open(self.root_path, flags)
        try:
            _set_cloexec(self._root_fd)
            self.root.read_raw('cgroup.controllers')
        except Exception:
            self.close()
            raise

    @property
    def root(self):
        return V2Node(self, ())

    def close(self):
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            # Best-effort cleanup for partial initialization and interpreter shutdown.
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _directory(self, parts):
        if self._root_fd is None:
            raise ValueError('The cgroup tree is closed')
        directory = os.dup(self._root_fd)
        try:
            _set_cloexec(directory)
            for name in parts:
                child = _open_at(directory, name, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
                os.close(directory)
                directory = child
            return directory
        except Exception:
            os.close(directory)
            raise

    def _require_writable(self):
        if not self.writable or not self.delegated:
            raise IOError(errno.EPERM, 'Tree is read-only; explicit writable and delegated flags are required')

    def get_node_by_path(self, path):
        parts = _parts(path)
        try:
            directory = self._directory(parts)
        except OSError as error:
            if error.errno == errno.ENOENT:
                return None
            raise
        os.close(directory)
        return V2Node(self, parts)

    def walk(self):
        return self.root.walk()


class V2Node(object):
    """A cgroup with native core files and resource-controller views."""

    def __init__(self, tree, parts):
        self.tree = tree
        self._parts = tuple(_name(part) for part in parts)

    @property
    def path(self):
        return '/' + '/'.join(self._parts)

    @property
    def full_path(self):
        return os.path.join(self.tree.root_path, *self._parts)

    @property
    def name(self):
        return self._parts[-1] if self._parts else '/'

    @property
    def parent(self):
        return V2Node(self.tree, self._parts[:-1]) if self._parts else None

    def __repr__(self):
        return '<V2Node {}>'.format(self.path)

    def read_raw(self, filename):
        filename = _filename(filename)
        directory = self.tree._directory(self._parts)
        try:
            try:
                descriptor = _open_at(directory, filename, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
            except (IOError, OSError) as error:
                path = os.path.join(self.full_path, filename)
                raise IOError(error.errno, 'Cannot read cgroup interface: {}'.format(error), path)
        finally:
            os.close(directory)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IOError(errno.EINVAL, 'Expected a regular cgroup interface file')
            chunks = []
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b''.join(chunks).decode('ascii')
        finally:
            os.close(descriptor)

    def write_records(self, filename, records):
        """Write one command per syscall, reporting any partial application.

        All records are validated before the first mutation.  Unknown filenames
        are permitted for future kernel interfaces, but must already exist.
        """
        self.tree._require_writable()
        filename = _filename(filename)
        if isinstance(records, (text_type, bytes)):
            raise TypeError('Expected an iterable of individual command records')
        records = [_text(record) for record in records]
        encoded = []
        for record in records:
            if any(char in record for char in '\n\r\x00'):
                raise ValueError('Each record must be one command')
            encoded.append((record + '\n').encode('ascii'))
        completed = []
        directory = self.tree._directory(self._parts)
        try:
            for record, data in zip(records, encoded):
                descriptor = None
                try:
                    descriptor = _open_at(directory, filename, os.O_WRONLY | os.O_TRUNC | getattr(os, 'O_NONBLOCK', 0))
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise IOError(errno.EINVAL, 'Expected a regular cgroup interface file')
                    # Nonblocking open avoids hanging on a substituted FIFO.
                    # Clear it after checking the inode: memory.high/max use
                    # O_NONBLOCK to change their reclaim/OOM write semantics.
                    file_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
                    fcntl.fcntl(descriptor, fcntl.F_SETFL,
                                file_flags & ~getattr(os, 'O_NONBLOCK', 0))
                    count = os.write(descriptor, data)
                    if count != len(data):
                        raise IOError(errno.EIO, 'Short cgroup command write')
                except (IOError, OSError) as error:
                    raise BatchWriteError(os.path.join(self.full_path, filename), completed, record, error)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                completed.append(record)
        finally:
            os.close(directory)

    def has_file(self, filename):
        directory = self.tree._directory(self._parts)
        try:
            try:
                return stat.S_ISREG(_stat_at(directory, _filename(filename)).st_mode)
            except OSError as error:
                if error.errno == errno.ENOENT:
                    return False
                raise
        finally:
            os.close(directory)

    @property
    def available_controllers(self):
        return frozenset(self.read_raw('cgroup.controllers').split())

    @property
    def enabled_controllers(self):
        """Controllers enabled for this cgroup's children, not for itself."""
        return frozenset(self.read_raw('cgroup.subtree_control').split())

    @property
    def capabilities(self):
        directory = self.tree._directory(self._parts)
        try:
            files = set()
            for name in _listdir(directory):
                try:
                    if stat.S_ISREG(_stat_at(directory, name).st_mode):
                        files.add(name)
                except OSError as error:
                    if error.errno != errno.ENOENT:
                        raise
        finally:
            os.close(directory)
        return {'version': 2, 'available_controllers': self.available_controllers,
                'enabled_controllers': self.enabled_controllers, 'files': frozenset(files),
                'write_enabled': self.tree.writable, 'delegated': self.tree.delegated}

    def enable_controllers(self, controllers):
        """Explicitly enable controllers for children; never move processes."""
        self._change_controllers(controllers, '+')

    def disable_controllers(self, controllers):
        """Explicitly disable controllers for children; kernel rules still apply."""
        self._change_controllers(controllers, '-')

    def _change_controllers(self, controllers, operation):
        if isinstance(controllers, (text_type, bytes)):
            raise TypeError('Expected an iterable of controller names')
        controllers = [_text(item) for item in controllers]
        available = self.available_controllers
        for name in controllers:
            if name not in available:
                raise ValueError('Controller is unavailable: {}'.format(name))
        if controllers:
            self.write_records('cgroup.subtree_control', [' '.join(operation + name for name in controllers)])

    @property
    def children(self):
        directory = self.tree._directory(self._parts)
        try:
            children = []
            for name in sorted(_listdir(directory)):
                try:
                    if stat.S_ISDIR(_stat_at(directory, name).st_mode):
                        children.append(V2Node(self.tree, self._parts + (_name(name),)))
                except OSError as error:
                    if error.errno != errno.ENOENT:
                        raise
            return children
        finally:
            os.close(directory)

    def walk(self):
        pending = [self]
        while pending:
            node = pending.pop()
            try:
                children = node.children
            except OSError as error:
                if error.errno == errno.ENOENT:
                    continue
                raise
            yield node
            pending.extend(reversed(children))

    def create_cgroup(self, name):
        self.tree._require_writable()
        name = _name(name)
        directory = self.tree._directory(self._parts)
        try:
            if os.mkdir in getattr(os, 'supports_dir_fd', ()):
                os.mkdir(name, dir_fd=directory)
            else:
                os.mkdir('/proc/self/fd/{}/{}'.format(directory, name))
        finally:
            os.close(directory)
        return V2Node(self.tree, self._parts + (name,))

    def delete_cgroup(self, name):
        self.tree._require_writable()
        name = _name(name)
        directory = self.tree._directory(self._parts)
        try:
            if os.rmdir in getattr(os, 'supports_dir_fd', ()):
                os.rmdir(name, dir_fd=directory)
            else:
                os.rmdir('/proc/self/fd/{}/{}'.format(directory, name))
        finally:
            os.close(directory)

    @property
    def procs(self):
        return set(int(item) for item in self.read_raw('cgroup.procs').split())

    @property
    def threads(self):
        return set(int(item) for item in self.read_raw('cgroup.threads').split())

    def attach_process(self, pid):
        self.attach_processes([pid])

    def attach_processes(self, pids):
        self.write_records('cgroup.procs', [_number(pid, 1) for pid in pids])

    def attach_thread(self, tid):
        self.attach_threads([tid])

    def attach_threads(self, tids):
        self.write_records('cgroup.threads', [_number(tid, 1) for tid in tids])

    @property
    def type(self):
        return self.read_raw('cgroup.type').strip()

    @property
    def events(self):
        return _flat(self.read_raw('cgroup.events'))

    @property
    def stat(self):
        return _flat(self.read_raw('cgroup.stat'))

    @property
    def frozen(self):
        """Requested freeze state; events['frozen'] reports actual completion."""
        return bool(int(self.read_raw('cgroup.freeze')))

    @frozen.setter
    def frozen(self, value):
        if not isinstance(value, bool):
            raise TypeError('Expected a boolean')
        self.write_records('cgroup.freeze', ['1' if value else '0'])

    @property
    def cpu(self):
        return V2CpuController(self)

    @property
    def memory(self):
        return V2MemoryController(self)

    @property
    def cpuset(self):
        return V2CpuSetController(self)

    @property
    def pids(self):
        return V2PidsController(self)

    @property
    def io(self):
        return V2IOController(self)


def _property(filename, parser=int, formatter=None):
    def get(controller):
        return parser(controller.node.read_raw(filename))

    def put(controller, value):
        controller.node.write_records(filename, [formatter(value)])

    return property(get, put if formatter is not None else None,
                    doc='Native {} interface.'.format(filename))


class _V2Controller(object):
    def __init__(self, node):
        self.node = node


class V2CpuController(_V2Controller):
    """Native CPU bandwidth and accounting; stat *_usec values are microseconds."""
    weight = _property('cpu.weight', int, lambda value: _number(value, 1, 10000))
    stat = _property('cpu.stat', _flat)
    pressure = _property('cpu.pressure', _pressure)

    @property
    def max(self):
        quota, period = self.node.read_raw('cpu.max').split()
        return CpuMax(_parse_limit(quota), int(period))

    @max.setter
    def max(self, value):
        quota, period = value
        quota = 'max' if quota is None else _number(quota, 1)
        self.node.write_records('cpu.max', ['{} {}'.format(quota, _number(period, 1000, 1000000))])


class V2MemoryController(_V2Controller):
    """Native byte-based memory limits; swap_max excludes resident memory."""
    current = _property('memory.current')
    min = _property('memory.min', _parse_limit, _limit)
    low = _property('memory.low', _parse_limit, _limit)
    high = _property('memory.high', _parse_limit, _limit)
    max = _property('memory.max', _parse_limit, _limit)
    swap_current = _property('memory.swap.current')
    swap_max = _property('memory.swap.max', _parse_limit, _limit)
    stat = _property('memory.stat', _flat)
    events = _property('memory.events', _flat)
    events_local = _property('memory.events.local', _flat)
    pressure = _property('memory.pressure', _pressure)


class V2CpuSetController(_V2Controller):
    cpus = _property('cpuset.cpus', _cpus, _format_cpus)
    mems = _property('cpuset.mems', _cpus, _format_cpus)
    cpus_effective = _property('cpuset.cpus.effective', _cpus)
    mems_effective = _property('cpuset.mems.effective', _cpus)


class V2PidsController(_V2Controller):
    """Kernel task counts and limits (including threads, not only processes)."""
    current = _property('pids.current')
    max = _property('pids.max', _parse_limit, _limit)
    peak = _property('pids.peak')
    events = _property('pids.events', _flat)
    events_local = _property('pids.events.local', _flat)


class V2IOController(_V2Controller):
    """Per-device native I/O limits: rbps/wbps in bytes/sec, riops/wiops in IOPS."""
    stat = _property('io.stat', _keyed)
    max = _property('io.max', _keyed)
    pressure = _property('io.pressure', _pressure)

    @property
    def weight(self):
        return _flat(self.node.read_raw('io.weight'))

    def set_weight(self, weight, device=None):
        device = 'default' if device is None else _device(device)
        self.node.write_records('io.weight', ['{} {}'.format(device, _number(weight, 1, 10000))])

    def set_max(self, device, **limits):
        device = _device(device)
        allowed = ('rbps', 'wbps', 'riops', 'wiops')
        if not limits or set(limits).difference(allowed):
            raise ValueError('Supply rbps, wbps, riops or wiops limits')
        fields = ['{}={}'.format(key, _limit(limits[key])) for key in allowed if key in limits]
        self.node.write_records('io.max', ['{} {}'.format(device, ' '.join(fields))])
