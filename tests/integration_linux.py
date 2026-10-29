"""Opt-in kernel integration, run only in disposable CI-owned hierarchies.

The v2 entrypoint must be launched by systemd-run with Delegate=yes. The v1
entrypoint must be launched in a private mount namespace (see CI configuration).
Neither test moves unrelated processes or changes host controller assignments.
"""
import errno
import os
import subprocess
import sys
import tempfile
import time
from collections import namedtuple

from cgroupspy.controllers import Controller
from cgroupspy.discovery import discover
from cgroupspy.v2 import BatchWriteError, CpuMax, V2Tree


def wait_until(predicate, message):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def check(condition, description):
    if not condition:
        raise AssertionError(description)
    print('PASS: ' + description, flush=True)


def v2():
    mounts = discover()
    candidates = [h.current_path for h in mounts if h.version == 2 and h.current_path]
    roots = [path for path in candidates
             if os.path.basename(path).startswith(b'cgroupspy-integration-')
             and path.endswith(b'.service')]
    if len(roots) != 1:
        raise RuntimeError('Run in the dedicated cgroupspy-integration-* delegated service')
    root_path = roots[0]
    with V2Tree(root_path, writable=True, delegated=True) as tree:
        root = tree.root
        # Only this interpreter runs directly in the service root. Move it to
        # its own child before enabling domain controllers for sibling groups.
        runner = root.create_cgroup('runner')
        runner.attach_process(os.getpid())
        check(os.getpid() in runner.procs, 'process membership uses the delegated subtree')
        required = set(['cpu', 'memory', 'cpuset', 'pids'])
        check(required.issubset(root.available_controllers), 'required test controllers are available')
        enabled = required | (set(['io']) & set(root.available_controllers))
        root.enable_controllers(sorted(enabled))
        check(enabled.issubset(root.enabled_controllers), 'explicit controller enablement')
        target = root.create_cgroup('workload')
        processes = []
        try:
            target.cpu.max = CpuMax(20000, 100000)
            target.cpu.weight = 200
            check(target.cpu.max == CpuMax(20000, 100000) and target.cpu.weight == 200,
                  'native CPU quota, period and weight round trip')
            target.memory.max = 128 * 1024 * 1024
            target.memory.high = 64 * 1024 * 1024
            check(target.memory.max == 128 * 1024 * 1024 and target.memory.high == 64 * 1024 * 1024,
                  'memory limits use bytes')
            if target.has_file('memory.swap.max'):
                target.memory.swap_max = None
                check(target.memory.swap_max is None, 'swap unlimited sentinel round trip')
            target.pids.max = 32
            check(target.pids.max == 32, 'task count limit round trip')
            target.cpuset.mems = root.cpuset.mems_effective
            target.cpuset.cpus = [min(root.cpuset.cpus_effective)]
            check(target.cpuset.cpus_effective == target.cpuset.cpus,
                  'requested and effective CPU masks agree in the test child')
            target.frozen = True
            wait_until(lambda: target.events['frozen'] == 1, 'freeze event not observed')
            target.frozen = False
            wait_until(lambda: target.events['frozen'] == 0, 'unfreeze event not observed')
            check(True, 'freeze requests complete through kernel events')

            # Gate CPU work until after migration, then verify actual throttling.
            before = target.cpu.stat['nr_throttled']
            busy = subprocess.Popen([sys.executable, '-c',
                'import sys,time; sys.stdin.read(1); end=time.process_time()+0.2\n'
                'while time.process_time()<end: pass'], stdin=subprocess.PIPE)
            processes.append(busy)
            target.attach_process(busy.pid)
            busy.stdin.write(b'g')
            busy.stdin.close()
            check(busy.wait(timeout=15) == 0, 'CPU-limited workload exits successfully')
            check(target.cpu.stat['nr_throttled'] > before, 'kernel enforces CPU bandwidth quota')

            for unused in range(2):
                processes.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']))
            sleepers = processes[-2:]
            target.attach_processes([process.pid for process in sleepers])
            check(all(process.pid in target.procs for process in sleepers),
                  'multi-process assignment migrates every record')
            check(target.pids.current >= 2, 'kernel task accounting sees migrated processes')
            with open('/proc/sys/kernel/pid_max') as stream:
                nonexistent_pid = int(stream.read())  # pid_max is an exclusive limit
            try:
                runner.attach_processes([sleepers[0].pid, nonexistent_pid])
            except BatchWriteError as error:
                check(error.completed == (str(sleepers[0].pid),) and
                      error.failed_record == str(nonexistent_pid) and error.errno == errno.ESRCH,
                      'kernel partial migration failure retains progress and errno')
            else:
                raise AssertionError('Expected ESRCH for an impossible process ID')

            if 'io' in enabled:
                devices = [name for name in os.listdir('/sys/block')
                           if not name.startswith(('loop', 'ram', 'dm-'))]
                if not devices:
                    raise AssertionError('No real block device for I/O command validation')
                with open('/sys/block/' + sorted(devices)[0] + '/dev') as stream:
                    device = stream.read().strip()
                target.io.set_max(device, rbps=10485760, wbps=None)
                check(target.io.max[device]['rbps'] == 10485760 and
                      target.io.max[device]['wbps'] is None,
                      'per-device I/O command and unlimited fields round trip')
            check('version' in target.capabilities and target.events['populated'] == 1,
                  'capability and lifecycle reads use actual kernel files')
            with V2Tree(root_path) as read_only:
                try:
                    read_only.root.memory.max = None
                except IOError as error:
                    check(error.errno == errno.EPERM, 'default view refuses mutation')
                else:
                    raise AssertionError('Read-only mutation succeeded')
        finally:
            target.frozen = False
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
            wait_until(lambda: target.events['populated'] == 0, 'workload remained populated')
            root.delete_cgroup('workload')
        check(tree.get_node_by_path('/workload') is None, 'empty workload cleanup')
    # systemd removes the runner subtree and service when this process exits.


def v1():
    # Mounting a named, controller-free v1 hierarchy does not detach any
    # resource controller from v2. Mount visibility is private; the temporary
    # named hierarchy is global but only our own child processes are moved.
    with tempfile.TemporaryDirectory(prefix='cgroupspy-v1-') as path:
        subprocess.check_call(['mount', '-t', 'cgroup', '-o',
                               'none,name=cgroupspy_test_{}'.format(os.getpid()), 'none', path])
        processes = []
        child_path = os.path.join(path, 'workload')
        try:
            os.mkdir(child_path)
            controller = Controller(namedtuple('Node', 'full_path')(child_path.encode()))
            for unused in range(2):
                processes.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']))
            controller.procs = [process.pid for process in processes]
            check(all(process.pid in controller.procs for process in processes),
                  'v1 cgroup.procs applies all PID records on the actual kernel')
            controller.tasks = [process.pid for process in processes]
            check(all(process.pid in controller.tasks for process in processes),
                  'v1 tasks applies all TID records on the actual kernel')
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
            if os.path.isdir(child_path):
                os.rmdir(child_path)
            subprocess.check_call(['umount', path])


if __name__ == '__main__':
    if sys.platform != 'linux' or os.environ.get('CGROUPSPY_RUN_LINUX_INTEGRATION') != '1':
        raise SystemExit('Explicit opt-in and an isolated Linux hierarchy are required')
    v1() if sys.argv[1:] == ['--v1'] else v2()
