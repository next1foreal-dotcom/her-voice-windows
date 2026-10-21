import importlib.util
from pathlib import Path
from contextlib import contextmanager
import uuid
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('install_progress', Path(__file__).resolve().parents[1] / 'scripts/install_progress.py')
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


@contextmanager
def workspace_temp():
    # Windows restricted tokens cannot re-open Python's owner-only mkdtemp ACL.
    folder = Path(__file__).resolve().parents[1] / '.runtime' / ('progress-test-' + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield str(folder)
    finally:
        lock = folder / '.lock'
        if lock.exists():
            lock.unlink()
        folder.rmdir()


class InstallProgressTests(unittest.TestCase):
    def test_failure_wins_over_live_outer_process(self):
        self.assertEqual(progress.classify({'state': 'failed'}, True, 0), 'failed')

    def test_no_output_is_distinct_from_waiting_and_progress(self):
        self.assertEqual(progress.classify({}, True, 120), 'no-output')
        self.assertEqual(progress.classify({}, True, 120, waiting=True), 'waiting-lock')
        self.assertEqual(progress.classify({}, True, 1), 'running')
        self.assertEqual(progress.classify({}, True, None), 'checking')

    def test_pid_reuse_is_not_a_live_installer(self):
        self.assertEqual(progress.classify({}, True, 0, identity_matches=False), 'stopped')

    def test_supervisor_cannot_stand_in_for_child(self):
        with workspace_temp() as folder, patch.object(progress, 'process_activity', return_value=None) as probe, patch.object(progress, 'lock_held', return_value=False):
            monitor = progress.PythonProgress(folder)
            result = monitor.snapshot({'pid': 123, 'worker_kind': 'supervisor', 'state': 'running'}, lambda _: '')
            probe.assert_called_with(None)
            self.assertEqual(result['python_state_code'], 'stopped')

    def test_actual_io_progress_resolves_stale_log(self):
        with workspace_temp() as folder, patch.object(progress, 'lock_held', return_value=True), patch.object(progress.time, 'time', side_effect=[1000, 1002]), patch.object(progress, 'process_activity', side_effect=[{'created': 100, 'counters': (1, 1)}, {'created': 100, 'counters': (1, 2)}]):
            monitor = progress.PythonProgress(folder)
            monitor.snapshot({'pid': 42}, lambda _: '')
            result = monitor.snapshot({'pid': 42}, lambda _: '')
            self.assertEqual(result['python_state_code'], 'running')
            self.assertEqual(result['python_activity_age_seconds'], 0)

    def test_status_heartbeat_does_not_hide_stalled_worker(self):
        with workspace_temp() as folder, patch.object(progress, 'lock_held', return_value=False), patch.object(progress.time, 'time', return_value=1000), patch.object(progress, 'process_activity', return_value={'created': 100, 'counters': (1, 1)}):
            monitor = progress.PythonProgress(folder)
            monitor.last_key = (42, 100, (1, 1), 0)
            monitor.last_activity = 800
            result = monitor.snapshot({'pid': 42, 'updated_at': 1000, 'state': 'running'}, lambda _: '')
            self.assertEqual(result['python_state_code'], 'no-output')

    def test_last_line_lock_wait_is_explicit(self):
        with workspace_temp() as folder, patch.object(progress, 'lock_held', return_value=True), patch.object(progress, 'process_activity', return_value={'created': 100, 'counters': (1, 1)}):
            result = progress.PythonProgress(folder).snapshot({'pid': 42}, lambda _: 'INFO Waiting to acquire exclusive lock for .venv-speech')
            self.assertEqual(result['python_state_code'], 'waiting-lock')

    def test_existing_unlocked_file_is_not_waiting(self):
        with workspace_temp() as folder:
            path = Path(folder) / '.lock'
            path.write_bytes(b'0')
            self.assertFalse(progress.lock_held(path))

    def test_real_held_lock_is_detected(self):
        import msvcrt
        with workspace_temp() as folder:
            path = Path(folder) / '.lock'
            path.write_bytes(b'0')
            with path.open('r+b') as stream:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    self.assertTrue(progress.lock_held(path))
                finally:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    unittest.main()
