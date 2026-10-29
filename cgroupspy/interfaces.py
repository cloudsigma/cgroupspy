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
DISCLAIMED. IN NO EVENT SHALL CloudSigma AG BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
try:
    from collections.abc import Iterable
except ImportError:
    from collections import Iterable
from cgroupspy.contenttypes import BaseContentType

try:
    string_types = (basestring,)
    integer_types = (int, long)
except NameError:
    string_types = (str,)
    integer_types = (int,)


def _integer_input(value):
    if isinstance(value, bytes) and not isinstance(value, string_types):
        value = value.decode('ascii')
    if not isinstance(value, integer_types + string_types):
        raise ValueError("Expected an integer or an integer string")
    return int(value)


class BatchWriteError(IOError):
    """A kernel command failed after zero or more records were applied.

    The original errno is retained. No rollback is attempted, since cgroup
    command files are not transactional.
    """

    def __init__(self, filename, failed_record, completed_records, original_error):
        self.failed_record = failed_record
        self.completed_records = tuple(completed_records)
        self.record_index = len(self.completed_records)
        self.original_error = original_error
        message = "Record {!r} failed after {} completed records: {}".format(
            failed_record, self.record_index, original_error)
        IOError.__init__(self, original_error.errno, message, filename)


class BaseFileInterface(object):

    """
    Basic cgroups file interface, implemented as a python descriptor. Provides means to get and set cgroup properties.
    """
    readonly = False
    writeonly = False
    write_records = False

    def __init__(self, filename, readonly=None, writeonly=None):
        try:
            self.filename = filename.encode()
        except AttributeError:
            self.filename = filename
        self.readonly = self.readonly if readonly is None else readonly
        self.writeonly = self.writeonly if writeonly is None else writeonly
        if self.readonly and self.writeonly:
            raise RuntimeError("This interface cannot be both readonly and writeonly")

    def __get__(self, instance, owner):
        if instance is None:
            return self
        if self.writeonly:
            raise RuntimeError("This interface is writeonly")

        value = instance.get_property(self.filename)
        return self.sanitize_get(value)

    def __set__(self, instance, value):
        if self.readonly:
            raise RuntimeError("This interface is readonly")

        value = self.sanitize_set(value)
        if value is not None:
            if self.write_records:
                # The sanitizer validates the complete batch before any writes.
                # Each set_property call opens/closes the file independently;
                # buffering cannot combine multiple kernel commands.
                result = None
                completed = []
                for record in value.splitlines():
                    if record.strip():
                        try:
                            result = instance.set_property(self.filename, record)
                        except (IOError, OSError) as error:
                            raise BatchWriteError(self.filename, record, completed, error)
                        completed.append(record)
                return result
            return instance.set_property(self.filename, value)

    def sanitize_get(self, value):
        return value

    def sanitize_set(self, value):
        return value


class FlagFile(BaseFileInterface):

    """
    Converts True/False to 1/0 and vise versa.
    """

    def sanitize_get(self, value):
        return bool(int(value))

    def sanitize_set(self, value):
        return int(bool(value))


class BitFieldFile(BaseFileInterface):

    """
    Example: '2' becomes [False, True, False, False, False, False, False, False]
    """

    def sanitize_get(self, value):
        v = int(value)
        # Calculate the length of the value in bits by converting to hex
        length = (len(hex(v)) - 2) * 4
        # Increase length to the next multiple of 8
        length += (7 - (length - 1) % 8)
        return [bool((v >> i) & 1) for i in range(length)]

    def sanitize_set(self, value):
        try:
            value = value.encode()
        except AttributeError:
            pass
        if isinstance(value, bytes) or not isinstance(value, Iterable):
            return int(value)
        return sum((int(bool(value[i])) << i) for i in range(len(value)))


class IntegerFile(BaseFileInterface):

    """
    Get/set single integer values.
    """

    def sanitize_get(self, value):
        val = int(value)
        if val == -1:
            val = None
        return val

    def sanitize_set(self, value):
        if value is None:
            value = -1
        return int(value)


class DictFile(BaseFileInterface):
    write_records = True

    def sanitize_get(self, value):
        res = {}
        for el in value.splitlines():
            if not el.strip():
                continue
            key, val = el.split()
            res[key] = int(val)
        return res

    def sanitize_set(self, value):
        if not isinstance(value, dict):
            raise ValueError("Value {} must be a dict".format(value))

        keys = sorted(value.keys())
        records = []
        for key in keys:
            if not str(key) or any(char.isspace() for char in str(key)):
                raise ValueError("Dictionary keys must be single words")
            records.append("{} {}".format(key, _integer_input(value[key])))
        return "\n".join(records)


class ListFile(BaseFileInterface):
    readonly = True

    def sanitize_get(self, value):
        return value.split()


class IntegerListFile(ListFile):

    """
    ex: 253237230463342 317756630269369 247294096796305 289833051422078
    """

    def sanitize_get(self, value):
        value_list = super(IntegerListFile, self).sanitize_get(value)
        return list(map(int, value_list))

    def sanitize_set(self, value):
        if value is None:
            return '-1'

        return " ".join([str(v) for v in value])


class CommaDashSetFile(BaseFileInterface):

    """
    Builds a set from files containing the following data format 'cpuset.cpus: 1-3,6,11-15',
    returning {1,2,3,5,11,12,13,14,15}
    """

    def sanitize_get(self, value):
        elems = []
        for el_group in value.strip().split(','):
            if "-" in el_group:
                start, end = el_group.split("-")
                start, end = int(start), int(end)
                if start < 0 or end < start:
                    raise ValueError("CPU/node ranges must be nonnegative and ascending")
                for el in range(start, end + 1):
                    elems.append(el)
            else:
                if el_group != '':
                    item = int(el_group)
                    if item < 0:
                        raise ValueError("CPU/node identifiers must be nonnegative")
                    elems.append(item)
        return set(elems)

    def sanitize_set(self, value):
        try:
            value = value.decode()
        except AttributeError:
            pass

        if isinstance(value, string_types):
            value = value.strip()
            if not value:
                return ' '
            value = self.sanitize_get(value)

        if isinstance(value, Iterable):
            value = set(value)
        else:
            raise ValueError("Value {} must be a sequence of int".format(value))

        for k in value:
            if not isinstance(k, integer_types) or k < 0:
                raise ValueError("Value {} must be a sequence of int".format(value))

        if not value:
            return ' '

        value = sorted(list(value))
        index = [i for i in range(len(value))]
        for i in range(len(value)):
            if index[i] != i:
                continue

            j = i

            while j < len(value) - 1:
                if value[j] + 1 == value[j + 1]:
                    index[j + 1] = i
                    j += 1
                else:
                    break

        parts = []
        for i in range(len(value)):
            if i > 0 and index[i - 1] == index[i]:
                continue

            j = i

            while j + 1 < len(value) and index[j + 1] == index[j]:
                j += 1

            if i == j:
                parts.append(str(value[i]))
            else:
                parts.append('{}-{}'.format(value[i], value[j]))

        return ','.join(parts)


class MultiLineIntegerFile(BaseFileInterface):
    write_records = True

    def sanitize_get(self, value):
        int_list = [int(val) for val in value.strip().split("\n") if val]
        return int_list

    def sanitize_set(self, value):
        if value is None:
            return '-1'
        if isinstance(value, bytes) and not isinstance(value, string_types):
            value = value.decode('ascii')
        if isinstance(value, string_types):
            value = value.split()
        elif isinstance(value, integer_types):
            value = [value]
        return '\n'.join(str(_integer_input(x)) for x in value)


class SplitValueFile(BaseFileInterface):
    """
    Example: Getting int(10) for file with value 'Total 10'. Readonly.
    """
    readonly = True

    def __init__(self, filename, position, restype=None, splitchar=" ", prefix="Total"):
        super(SplitValueFile, self).__init__(filename)
        self.position = position
        self.restype = restype
        self.splitchar = splitchar
        self.prefix = prefix

    def sanitize_get(self, value):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        # Block I/O files contain per-device operation rows before the aggregate.
        # Match the standalone prefix, never a device's own "Total" field.
        matching = [line for line in lines if line.split()[0] == self.prefix]
        if matching:
            line = matching[-1]
        elif len(lines) == 1:
            # Retain the original generic single-line interface behavior.
            line = lines[0]
        else:
            raise ValueError("No {} aggregate in {}".format(self.prefix, self.filename))
        if self.splitchar == ' ':
            res = line.split()[self.position]
        else:
            res = line.split(self.splitchar)[self.position]
        if self.restype and not isinstance(res, self.restype):
            return self.restype(res)
        return res

    def sanitize_set(self, value):
        return '{}{}{}'.format(self.prefix, self.splitchar, value)


class TypedFile(BaseFileInterface):

    def __init__(self, filename, contenttype, readonly=None, writeonly=None, many=False):
        if not issubclass(contenttype, BaseContentType):
            raise RuntimeError("Contenttype should be a class inheriting "
                               "from BaseContentType, not {}".format(contenttype))

        self.contenttype = contenttype
        self.many = many
        self.write_records = many
        super(TypedFile, self).__init__(filename, readonly=readonly, writeonly=writeonly)

    def sanitize_set(self, value):
        if isinstance(value, self.contenttype):
            # Content objects are mutable: validate their current serialized
            # fields too, rather than trusting construction-time validation.
            return str(self.contenttype.from_string(str(value)))

        if self.many:
            items = []
            if isinstance(value, bytes) and not isinstance(value, string_types):
                value = value.decode('ascii')
            if isinstance(value, string_types):
                value = value.splitlines()
            for entry in value:
                if isinstance(entry, bytes) and not isinstance(entry, string_types):
                    entry = entry.decode('ascii')
                if isinstance(entry, string_types):
                    entry = entry.strip()
                    if not entry:
                        continue
                    items.append(str(self.contenttype.from_string(entry)))
                else:
                    items.append(str(self.contenttype.from_string(str(entry))))
            return "\n".join(items)
        else:
            return str(self.contenttype.from_string(value))

    def sanitize_get(self, value):
        if self.many:
            result = []
            for line in value.splitlines():
                line = line.strip()
                if not line:
                    continue
                result.append(self.contenttype.from_string(line))
            return result

        return self.contenttype.from_string(value)


class DictOrFlagFile(BaseFileInterface):
    def __init__(self, filename, readonly=None, writeonly=None):
        super(DictOrFlagFile, self).__init__(filename, readonly=readonly, writeonly=writeonly)
        self.interfaces = {
            'dict': DictFile(filename),
            'flag': FlagFile(filename),
        }

    def sanitize_get(self, value):
        try:
            return self.interfaces['dict'].sanitize_get(value)
        except (TypeError, ValueError):
            return self.interfaces['flag'].sanitize_get(value)

    def sanitize_set(self, value):
        if isinstance(value, dict):
            return self.interfaces['dict'].sanitize_set(value)
        if value is None or isinstance(value, (bool,) + integer_types):
            return self.interfaces['flag'].sanitize_set(value)
        raise ValueError("Value must be a dictionary or a flag")


class OomControlFile(DictOrFlagFile):
    """Read OOM status as a dictionary, write only the disable flag."""

    def sanitize_set(self, value):
        if isinstance(value, dict):
            if set(value) != set(['oom_kill_disable']):
                raise ValueError("Only oom_kill_disable can be written to OOM control")
            value = value['oom_kill_disable']
        if value is not None and not isinstance(value, (bool,) + integer_types):
            raise ValueError("OOM disable flag must be a boolean or integer")
        return self.interfaces['flag'].sanitize_set(value)
