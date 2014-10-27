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
from collections import Iterable


class BaseFileInterface(object):

    """
    Basic cgroups file interface, implemented as a python descriptor. Provides means to get and set cgroup properties.
    """

    def __init__(self, filename):
        self.filename = filename

    def __get__(self, instance, owner):
        value = instance.get_property(self.filename)
        return self.sanitize_get(value)

    def __set__(self, instance, value):
        value = self.sanitize_set(value)
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

    def sanitize_get(self, value):
        res = {}
        for el in value.split("\n"):
            key, val = el.split()
            res[key] = int(val)
        return res


class ListFile(BaseFileInterface):

    def sanitize_get(self, value):
        return value.split()


class IntegerListFile(ListFile):

    """
    ex: 253237230463342 317756630269369 247294096796305 289833051422078
    """

    def sanitize_get(self, value):
        value_list = super(IntegerListFile, self).sanitize_get(value)
        return map(int, value_list)


class CommaDashSetFile(BaseFileInterface):

    """
    Builds a set from files containig the following data format 'cpuset.cpus: 1-3,6,11-15',
    returning {1,2,3,5,11,12,13,14,15}
    """

    def sanitize_get(self, value):
        elems = []
        for el_group in value.strip().split(','):
            if "-" in el_group:
                start, end = el_group.split("-")
                for el in xrange(int(start), int(end) + 1):
                    elems.append(el)
            else:
                elems.append(int(el_group))
        return set(elems)

    def sanitize_set(self, value):
        if isinstance(value, basestring) or not isinstance(value, Iterable):
            value = [str(value)]
        return ",".join(str(x) for x in value)


class MultiLineIntegerFile(BaseFileInterface):

    def sanitize_get(self, value):
        int_list = [int(val) for val in value.strip().split("\n") if val]
        return int_list
