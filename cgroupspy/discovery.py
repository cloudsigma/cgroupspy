"""Read-only discovery of cgroup mounts in the current mount namespace.

No directory is created and no controller is enabled by this module.  Inputs
may be injected as text, bytes, or readable file objects for offline inspection.
Filesystem paths are bytes, matching the existing cgroupspy path contract.
"""
from __future__ import absolute_import

import posixpath
import re
import sys


_MOUNT_ESCAPE = re.compile(br"\\([0-7]{3})")
_MOUNT_FLAGS = frozenset((
    b"rw", b"ro", b"relatime", b"strictatime", b"noatime", b"nodiratime",
    b"nosuid", b"nodev", b"noexec", b"sync", b"async", b"dirsync",
    b"seclabel", b"xattr", b"noprefix", b"clone_children", b"none",
    b"cpuset_v2_mode", b"favordynmods", b"__DEVEL__sane_behavior", b"sane_behavior",
))


def _bytes(value):
    if isinstance(value, bytes):
        return value
    return value.encode(sys.getfilesystemencoding() or "utf-8")


def _read(value, default_path):
    if value is None:
        with open(default_path, "rb") as stream:
            return stream.read()
    if hasattr(value, "read"):
        value = value.read()
    return _bytes(value)


def _unescape(value):
    return _MOUNT_ESCAPE.sub(lambda m: bytes(bytearray([int(m.group(1), 8)])), value)


def _relative_to(path, root):
    # A proc path outside a namespace-visible mount is not safely resolvable.
    if not path.startswith(b"/") or b".." in path.split(b"/"):
        return None
    path = posixpath.normpath(path)
    root = posixpath.normpath(root)
    if path == root:
        return b""
    prefix = root.rstrip(b"/") + b"/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return None


class Hierarchy(object):
    """One namespace-visible cgroup filesystem mount.

    ``controllers`` describes v1 mount options, not v2 controller availability.
    Inspect ``V2Node.available_controllers`` for the latter.  ``process_path``
    is the path reported by /proc/self/cgroup; ``current_path`` resolves it
    within this mount, or is None if it cannot be resolved without guessing.
    """

    def __init__(self, version, mountpoint, mount_root=b"/", controllers=(),
                 process_path=None, options=()):
        self.version = version
        self.mountpoint = _bytes(mountpoint)
        self.mount_root = _bytes(mount_root)
        self.controllers = frozenset(controllers)
        self.process_path = None if process_path is None else _bytes(process_path)
        self.options = frozenset(options)

    @property
    def current_path(self):
        if self.process_path is None:
            return None
        relative = _relative_to(self.process_path, self.mount_root)
        if relative is None:
            return None
        return posixpath.join(self.mountpoint, relative).rstrip(b"/") or b"/"

    @property
    def read_only(self):
        return b"ro" in self.options

    def __repr__(self):
        return "<Hierarchy v{0} {1!r}>".format(self.version, self.mountpoint)


def discover(mountinfo=None, cgroup=None):
    """Return cgroup v1/v2 mounts, including co-mounts and bind-mounted roots.

    With no arguments, read /proc/self/mountinfo and /proc/self/cgroup.  When
    mountinfo is supplied and cgroup is omitted, do not mix host membership
    with the injected mount snapshot.  Malformed nonempty relevant records
    raise ValueError rather than producing misleading hierarchy descriptions.
    No v2 filesystem files are read during parsing.
    """
    live = mountinfo is None
    mounts = _read(mountinfo, "/proc/self/mountinfo")
    memberships = _read(cgroup, "/proc/self/cgroup") if live or cgroup is not None else b""
    paths = {}
    unified_path = None
    for line in memberships.splitlines():
        if not line.strip():
            continue
        fields = line.split(b":", 2)
        if len(fields) != 3:
            raise ValueError("Malformed /proc/self/cgroup record: {0!r}".format(line))
        if fields[0] == b"0" and not fields[1]:
            unified_path = fields[2]
        else:
            for controller in fields[1].split(b","):
                paths[controller] = fields[2]

    result = []
    for line in mounts.splitlines():
        if not line.strip():
            continue
        halves = line.split(b" - ", 1)
        if len(halves) != 2:
            raise ValueError("Malformed mountinfo record: {0!r}".format(line))
        before, after = halves[0].split(), halves[1].split()
        if not after:
            raise ValueError("Malformed mountinfo record: {0!r}".format(line))
        filesystem = after[0]
        if filesystem not in (b"cgroup", b"cgroup2"):
            continue
        if len(before) < 6 or len(after) < 3:
            raise ValueError("Malformed cgroup mountinfo record: {0!r}".format(line))
        options = set(before[5].split(b",")) | set(after[2].split(b","))
        version = 2 if filesystem == b"cgroup2" else 1
        controllers = []
        process_path = unified_path if version == 2 else None
        if version == 1:
            for option in after[2].split(b","):
                if option in _MOUNT_FLAGS or option.startswith(b"release_agent="):
                    continue
                controllers.append(option.decode("ascii"))
            matching = set(paths[c.encode("ascii")] for c in controllers
                           if c.encode("ascii") in paths)
            if len(matching) == 1:
                process_path = matching.pop()
            elif len(matching) > 1:
                raise ValueError("Inconsistent membership for co-mounted controllers")
        result.append(Hierarchy(version, _unescape(before[4]), _unescape(before[3]),
                                controllers, process_path, options))
    return result


def controller_map(hierarchies, root_path=b"/sys/fs/cgroup"):
    """Map immediate v1 mount directories to controller names for legacy trees.

    Nested or unrelated mountpoints and v2 mounts are excluded.  Pass this
    mapping to Tree/GroupedTree's ``controller_map`` argument.  It also handles
    custom v1 mountpoint names without guessing their controller identity.
    """
    root_path = posixpath.normpath(_bytes(root_path))
    mapping = {}
    for hierarchy in hierarchies:
        path = posixpath.normpath(hierarchy.mountpoint)
        if hierarchy.version == 1 and posixpath.dirname(path) == root_path:
            mapping[posixpath.basename(path)] = tuple(sorted(hierarchy.controllers))
    return mapping
