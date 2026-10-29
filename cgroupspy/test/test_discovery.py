from __future__ import absolute_import

import io
import unittest

from cgroupspy.discovery import Hierarchy, controller_map, discover


class DiscoveryTests(unittest.TestCase):
    def test_combined_and_unified_mounts(self):
        mounts = (
            "21 1 0:20 / /sys/fs/cgroup/cpu,cpuacct rw,nosuid - cgroup cgroup rw,cpu,cpuacct\n"
            "22 1 0:21 / /sys/fs/cgroup/unified rw - cgroup2 cgroup rw,nsdelegate\n"
            "23 1 8:1 / /home rw - ext4 /dev/sda rw\n"
        )
        groups = "3:cpu,cpuacct:/machine/guest\n0::/user.slice/test.service\n"
        legacy, unified = discover(mounts, groups)
        self.assertEqual(legacy.version, 1)
        self.assertEqual(legacy.controllers, frozenset(["cpu", "cpuacct"]))
        self.assertEqual(legacy.current_path, b"/sys/fs/cgroup/cpu,cpuacct/machine/guest")
        self.assertEqual(unified.version, 2)
        self.assertEqual(unified.controllers, frozenset())
        self.assertEqual(unified.current_path, b"/sys/fs/cgroup/unified/user.slice/test.service")

    def test_mount_escapes_and_optional_fields(self):
        mounts = b"21 1 0:20 /some\\040root /my\\040mount rw shared:4 master:1 - cgroup cgroup rw,memory\n"
        hierarchy = discover(mounts, b"4:memory:/some root/child\n")[0]
        self.assertEqual(hierarchy.mount_root, b"/some root")
        self.assertEqual(hierarchy.mountpoint, b"/my mount")
        self.assertEqual(hierarchy.current_path, b"/my mount/child")

    def test_backslash_escape(self):
        hierarchy = discover(b"21 1 0:20 / /my\\134mount rw - cgroup2 cgroup rw\n")[0]
        self.assertEqual(hierarchy.mountpoint, b"/my\\mount")

    def test_injected_mounts_do_not_read_live_membership(self):
        hierarchy = discover("21 1 0:20 / /cg rw - cgroup2 cgroup rw\n")[0]
        self.assertIsNone(hierarchy.process_path)
        self.assertIsNone(hierarchy.current_path)

    def test_bind_root_boundary(self):
        h = Hierarchy(2, b"/cg", b"/slice")
        for path, expected in [(b"/slice", b"/cg"), (b"/slice/a", b"/cg/a"),
                               (b"/slicex/a", None), (b"/elsewhere", None),
                               (b"/slice/../secret", None), (b"relative", None)]:
            h.process_path = path
            self.assertEqual(h.current_path, expected)

    def test_root_mount(self):
        h = Hierarchy(2, b"/", b"/", process_path=b"/")
        self.assertEqual(h.current_path, b"/")

    def test_ro_on_either_option_list(self):
        for before, after in [(b"ro", b"rw"), (b"rw", b"ro")]:
            raw = b"21 1 0:20 / /cg " + before + b" - cgroup2 cgroup " + after
            self.assertTrue(discover(raw)[0].read_only)

    def test_filelike_input(self):
        h = discover(io.BytesIO(b"21 1 0:20 / /cg rw - cgroup cgroup rw,memory\n"),
                     io.BytesIO(b"4:memory:/\n"))[0]
        self.assertEqual(h.current_path, b"/cg")

    def test_named_hierarchy(self):
        h = discover(b"21 1 0:20 / /cg rw - cgroup cgroup rw,name=systemd,xattr\n",
                     b"1:name=systemd:/service\n")[0]
        self.assertEqual(h.controllers, frozenset(["name=systemd"]))
        self.assertEqual(h.current_path, b"/cg/service")

    def test_legacy_mount_flags_are_not_controllers(self):
        h = discover(b"21 1 0:20 / /cg rw - cgroup cgroup rw,cpuset,cpuset_v2_mode,favordynmods,__DEVEL__sane_behavior\n")[0]
        self.assertEqual(h.controllers, frozenset(["cpuset"]))

    def test_controller_map_uses_only_direct_v1_mounts(self):
        hs = [Hierarchy(1, b"/cg/custom", controllers=["cpuacct", "cpu"]),
              Hierarchy(2, b"/cg/v2"), Hierarchy(1, b"/cg/nested/cpu", controllers=["cpu"]),
              Hierarchy(1, b"/other/cpu", controllers=["cpu"])]
        self.assertEqual(controller_map(hs, b"/cg/"), {b"custom": ("cpu", "cpuacct")})

    def test_malformed_records(self):
        for mounts in [b"malformed", b"21 1 0:20 / /cg rw - cgroup2\n"]:
            with self.assertRaises(ValueError):
                discover(mounts)
        with self.assertRaises(ValueError):
            discover(b"", b"bad-membership")
        with self.assertRaises(ValueError):
            discover(b"21 1 0:20 / /cg rw - cgroup cgroup rw,cpu,cpuacct\n",
                     b"1:cpu:/a\n2:cpuacct:/b\n")


if __name__ == "__main__":
    unittest.main()
