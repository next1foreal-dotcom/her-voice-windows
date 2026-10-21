"""Classify installer evidence without treating an outer wrapper as progress."""
import ctypes
from ctypes import wintypes
import errno
import os
from pathlib import Path
import time

LABELS = {'passed': '已完成', 'failed': '失敗，需處理', 'stopped': '程序已停止',
          'waiting-lock': '等待環境或快取鎖', 'no-output': '無新輸出／活動，需檢查',
          'running': '安裝有實際活動', 'checking': '正在核對活動'}


def classify(data, worker_alive, age, waiting=False, identity_matches=True):
    if data.get('state') == 'failed':
        return 'failed'
    if data.get('state') == 'passed':
        return 'passed'
    if not worker_alive or not identity_matches:
        return 'stopped'
    if waiting:
        return 'waiting-lock'
    if age is None:
        return 'checking'
    return 'no-output' if age >= 90 else 'running'


def lock_held(path):
    """Probe the OS lock; the existence of a .lock file is not evidence."""
    if not path.exists():
        return False
    import msvcrt
    try:
        with path.open('r+b') as stream:
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                return True if error.errno in (errno.EACCES, errno.EDEADLK, errno.EAGAIN) else None
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return None


def process_activity(pid):
    if not pid or os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]
    kernel.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters)]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in (created, exited, system, user)]):
            return None
        birth = ((created.dwHighDateTime << 32) + created.dwLowDateTime) / 1e7 - 11644473600
        counters = Counters()
        if not kernel.GetProcessIoCounters(handle, ctypes.byref(counters)):
            return dict(created=birth, counters=None)
        return dict(created=birth, counters=(counters.read_bytes, counters.write_bytes))
    finally:
        kernel.CloseHandle(handle)


class PythonProgress:
    def __init__(self, runtime):
        self.root = Path(runtime)
        self.last_key = None
        self.last_activity = None

    def snapshot(self, data, tail):
        now = time.time()
        # A supervising shell's PID must never substitute for the actual worker.
        pid = data.get('child_pid') or (data.get('pid') if data.get('worker_kind') != 'supervisor' else None)
        activity = process_activity(pid)
        expected = data.get('child_started_at') if data.get('child_pid') else data.get('process_started_at')
        matches = activity is not None and (not expected or abs(activity['created'] - float(expected)) < 2)
        log_name = data.get('log_path', 'python-install-retry.log')
        log_path = self.root / log_name
        if not log_path.resolve().is_relative_to(self.root.resolve()):
            log_path = self.root / 'python-install-retry.log'
        try:
            info = log_path.stat()
            log_age = max(0, now - info.st_mtime)
            log_size = info.st_size
        except OSError:
            log_age, log_size = None, 0
        text = tail(log_path)
        lines = text.splitlines()
        waiting = bool(lines and 'Waiting to acquire exclusive lock' in lines[-1])
        # Only compare counters after seeing the same PID/creation identity twice.
        key = (pid, activity['created'], activity['counters'], log_size) if matches else None
        if key is not None and self.last_key is not None and key[:2] == self.last_key[:2] and key != self.last_key:
            self.last_activity = now
        if self.last_key is None or (key and key[:2] != self.last_key[:2]):
            self.last_activity = now - log_age if log_age is not None else None
        self.last_key = key
        age = max(0, now - self.last_activity) if self.last_activity is not None else None
        state = classify(data, activity is not None, age, waiting, matches)
        env_lock = lock_held(self.root.parent / '.venv-speech/.lock')
        return dict(python_state=LABELS[state], python_state_code=state, python_worker_pid=pid,
                    python_log_age_seconds=round(log_age) if log_age is not None else None,
                    python_activity_age_seconds=round(age) if age is not None else None,
                    python_environment_lock=env_lock, python_log_tail='\n'.join(lines[-5:]))
