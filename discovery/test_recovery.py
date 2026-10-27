"""Failure-injection checks; no household devices or live playlists are used."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from common import media_fingerprint
from server import Worker


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.worker = Worker(Path(temp.name) / 'state.json', '192.0.2.1')
        self.base = [{'endpoint': '239.255.42.1:8000', 'protocol': 'udp', 'name': 'Example A'}]
        self.worker.ingest({'baseline': self.base})
        self.worker.set_link({'ready': True, 'generation': 'synthetic-link'})

    def prepare_audit(self):
        self.worker.state['audit'] = {
            'targets': ['239.255.42.1:8000', '239.255.42.2:8000'],
            'state': 'paused', 'done': 1, 'total': 2, 'started': 100,
            'counts': {'media': 1},
        }
        self.worker.save()

    def test_scheduled_audit_rebuilds_all_current_targets(self):
        self.prepare_audit()
        self.worker.ingest({'baseline': self.base + [dict(self.base[0], endpoint='239.255.42.3:8000')]})
        with patch('server.threading.Thread') as thread:
            self.worker.start('audit', {'trigger': 'scheduled'})
        try:
            self.assertEqual(thread.call_args.kwargs['args'][1], ['239.255.42.1:8000', '239.255.42.3:8000'])
            self.assertEqual(self.worker.job['done'], 0)
            audit = self.worker.status()['audit']
            self.assertEqual(audit['counts'], {})
            self.assertEqual(audit['previous']['done'], 1)
            self.assertEqual(audit['previous']['state'], 'paused')
            self.assertNotIn('targets', audit['previous'])
        finally:
            self.worker.job_lock.release()

    def test_manual_audit_still_resumes_saved_progress(self):
        self.prepare_audit()
        with patch('server.threading.Thread') as thread:
            self.worker.start('audit', {})
        try:
            self.assertEqual(self.worker.job['done'], 1)
            self.assertEqual(thread.call_args.kwargs['args'][1], ['239.255.42.1:8000', '239.255.42.2:8000'])
        finally:
            self.worker.job_lock.release()

    def test_explicit_new_audit_restarts_without_clearing_evidence(self):
        self.prepare_audit()
        self.worker.state['observations']['239.255.42.3:8000'] = {'validation': 'no_response'}
        before = copy.deepcopy(self.worker.state['observations'])
        with patch('server.threading.Thread') as thread:
            self.worker.start('audit', {'mode': 'new'})
        try:
            self.assertEqual(self.worker.job['done'], 0)
            self.assertEqual(self.worker.state['observations'], before)
            self.assertEqual(thread.call_args.kwargs['args'][1], ['239.255.42.1:8000', '239.255.42.3:8000'])
            self.assertEqual(self.worker.state['audit']['trigger'], 'manual')
            self.assertEqual(self.worker.state['audit']['previous']['done'], 1)
        finally:
            self.worker.job_lock.release()

    def test_explicit_resume_does_not_silently_start_new_scan(self):
        with patch('server.threading.Thread') as thread:
            with self.assertRaisesRegex(ValueError, '没有可继续'):
                self.worker.start('audit', {'mode': 'resume'})
            thread.assert_not_called()
        self.assertFalse(self.worker.job_lock.locked())
        self.prepare_audit()
        with patch('server.threading.Thread'):
            self.worker.start('audit', {'mode': 'resume'})
        self.assertEqual(self.worker.job['done'], 1)
        self.worker.job_lock.release()

    def test_invalid_audit_mode_leaves_saved_progress(self):
        self.prepare_audit()
        before = copy.deepcopy(self.worker.state)
        with self.assertRaises(ValueError):
            self.worker.start('audit', {'mode': 'delete'})
        self.assertEqual(self.worker.state, before)
        self.assertFalse(self.worker.job_lock.locked())

    def test_disk_full_does_not_acknowledge_name_confirmation(self):
        key = '239.255.42.2:8000'
        self.worker.observe(key, {'validation': 'media', 'protocol': 'udp', 'programs': [],
                                 'streams': [{'type': 'video', 'codec': 'h264'}]})
        self.worker.save()
        before = copy.deepcopy(self.worker.state)
        payload = {'endpoint': key, 'name': 'Example B', 'reviewed': True,
                   'fingerprint': media_fingerprint(key, before['observations'][key])}
        with patch('server.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.worker.confirm_name(payload)
        self.assertEqual(self.worker.state, before)
        self.assertTrue(self.worker.status()['storage_error'])
        self.assertFalse(self.worker.rows()[0]['name_confirmed'])
        self.worker.save()
        self.assertFalse(self.worker.status()['storage_error'])
        self.assertEqual(Worker(self.worker.path, self.worker.router).state, before)

    def test_worker_releases_lock_when_state_and_history_writes_fail(self):
        with patch('server.threading.Thread'):
            self.worker.start('audit', {})
        before = copy.deepcopy(self.worker.state)
        with patch('server.atomic_json', side_effect=OSError('read only')), \
                patch.object(self.worker, 'busy', return_value=False), \
                patch.object(self.worker, 'probe', return_value={'validation': 'no_response'}):
            self.worker.run('audit', [self.base[0]['endpoint']])
        self.assertFalse(self.worker.job_lock.locked())
        self.assertEqual(self.worker.job['state'], 'error')
        self.assertEqual(self.worker.job['done'], 0)
        self.assertEqual(self.worker.state['observations'], before['observations'])
        self.assertEqual(self.worker.state['audit']['done'], 0)
        self.assertTrue(self.worker.status()['storage_error'])
        with patch('server.threading.Thread'):
            self.worker.start('audit', {})
        self.worker.job_lock.release()
        self.assertFalse(self.worker.status()['storage_error'])

    def test_final_history_failure_cannot_leave_job_complete(self):
        self.worker.job_lock.acquire()
        self.worker.job = {'state': 'running', 'kind': 'verify', 'total': 0, 'done': 0, 'started': time.time()}
        with patch('server.atomic_json', side_effect=OSError('disk full')):
            self.worker.run('verify', [])
        self.assertFalse(self.worker.job_lock.locked())
        self.assertEqual(self.worker.job['state'], 'error')
        self.assertTrue(self.worker.status()['storage_error'])

    def test_storage_recovery_resumes_last_saved_cursor(self):
        self.prepare_audit()
        with patch('server.threading.Thread'):
            self.worker.start('audit', {})
        with patch('server.atomic_json', side_effect=OSError('disk full')), \
                patch.object(self.worker, 'busy', return_value=False), \
                patch.object(self.worker, 'probe', return_value={'validation': 'no_response'}):
            self.worker.run('audit', ['239.255.42.1:8000', '239.255.42.2:8000'])
        self.assertEqual(self.worker.state['audit']['state'], 'error')
        self.assertEqual(self.worker.state['audit']['done'], 1)
        self.assertEqual(self.worker.state['audit']['counts'], {'media': 1})
        with patch('server.threading.Thread'):
            self.worker.start('audit', {})
        try:
            self.assertEqual(self.worker.job['done'], 1)
            self.assertFalse(self.worker.storage_error)
        finally:
            self.worker.job_lock.release()

    def test_router_refuses_update_while_nas_cannot_save(self):
        import router
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(router, 'RUNTIME', Path(directory)), \
                patch.object(router, 'legacy_busy', return_value=False), \
                patch.object(router, 'nas_request', return_value={'storage_error': 'unavailable'}), \
                patch.object(router, 'apply_playlist') as apply:
            router.reconcile({'auto_update': True})
            apply.assert_not_called()
            self.assertIn('error', (Path(directory) / 'update.json').read_text())

    def test_thread_start_failure_marks_audit_recoverable(self):
        with patch('server.threading.Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('no worker thread')
            with self.assertRaises(RuntimeError):
                self.worker.start('audit', {})
        self.assertFalse(self.worker.job_lock.locked())
        self.assertEqual(self.worker.job['state'], 'error')
        self.assertEqual(self.worker.state['audit']['state'], 'paused')
        self.assertEqual(Worker(self.worker.path, self.worker.router).state['audit']['state'], 'paused')


if __name__ == '__main__':
    unittest.main()
