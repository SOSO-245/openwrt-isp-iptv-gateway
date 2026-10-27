"""Post-commit acknowledgement failures must not repeat local playlist writes."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router


class UpdateSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / 'runtime'
        self.www = self.root / 'www'
        self.runtime.mkdir()
        self.www.mkdir()
        for name in ('new.m3u', 'new-logo.m3u'):
            (self.www / name).write_text('#EXTM3U\nold playlist\n')
        for name, value in [('RUNTIME', self.runtime), ('WWW', self.www)]:
            patcher = patch.object(router, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        busy = patch.object(router, 'legacy_busy', return_value=False)
        busy.start()
        self.addCleanup(busy.stop)
        self.c = {'auto_update': True}
        self.now = 2000000000
        self.result = {'state': 'applied', 'at': self.now, 'changes': [{'kind': 'add'}],
                       'backup': str(self.root / 'backups' / 'example')}

    def update(self):
        return json.loads((self.runtime / 'update.json').read_text())

    def test_recent_updates_only_shows_committed_journals_without_private_fields(self):
        backups = self.root / 'backups'
        for index, state in enumerate(('applied', 'rolled_back', 'prepared', 'applied')):
            folder = backups / f'update-{index:03}'
            folder.mkdir(parents=True)
            (folder / 'manifest.json').write_text(json.dumps({'at': index, 'state': state,
                'changes': [{'kind': 'add', 'name': 'Example', 'after': 'PRIVATE-ENDPOINT'}],
                'private': 'PRIVATE'}))
        broken = backups / 'update-999'
        broken.mkdir()
        (broken / 'manifest.json').write_text('invalid')
        rows = router.recent_updates(backups)
        self.assertEqual([r['at'] for r in rows], [3, 0])
        self.assertNotIn('PRIVATE', json.dumps(rows))
        self.assertEqual(router.recent_updates(self.root / 'absent'), [])

    def apply(self, *_):
        for name in ('new.m3u', 'new-logo.m3u'):
            (self.www / name).write_text('#EXTM3U\nupdated playlist\n')
        return dict(self.result)

    def failed_sync(self):
        def request(_c, path, data=None):
            if path == '/v1/status':
                return {'rows': [], 'job': {'state': 'idle'}}
            self.assertEqual(path, '/v1/sample')
            self.assertEqual(self.update()['state'], 'applied')
            self.assertTrue(self.update()['sync_pending'])
            raise TimeoutError('private downstream details')
        with patch('router.nas_request', side_effect=request), \
                patch('router.baseline_report', return_value={'baseline': []}), \
                patch('router.apply_playlist', side_effect=self.apply) as apply, \
                patch('router.time.time', return_value=self.now):
            router.reconcile(self.c)
        self.assertEqual(apply.call_count, 1)

    def test_local_success_is_recorded_before_failed_acknowledgement(self):
        self.failed_sync()
        result = self.update()
        self.assertEqual(result['state'], 'applied')
        self.assertTrue(result['sync_pending'])
        self.assertEqual(result['last_applied']['backup'], self.result['backup'])
        self.assertNotIn('private downstream details', str(result))
        self.assertFalse((self.runtime / 'update-attempt.json').exists())
        self.assertIn('updated playlist', (self.www / 'new.m3u').read_text())

    def test_retry_waits_five_minutes_and_does_not_reapply(self):
        self.failed_sync()
        with patch('router.time.time', return_value=self.now+299), \
                patch('router.nas_request', return_value={'rows': []}) as api, \
                patch('router.apply_playlist') as apply:
            router.reconcile(self.c)
        self.assertEqual([c.args[1] for c in api.call_args_list], ['/v1/status'])
        apply.assert_not_called()

    def test_recovery_syncs_current_baseline_without_rewriting_files(self):
        self.failed_sync()
        snapshot = {p.name: p.read_bytes() for p in self.www.iterdir()}
        with patch('router.time.time', return_value=self.now+300), \
                patch('router.nas_request', return_value={'rows': []}) as api, \
                patch('router.baseline_report', return_value={'baseline': ['current-files']}) as baseline, \
                patch('router.apply_playlist') as apply:
            router.reconcile(self.c)
        self.assertEqual(api.call_args.args[2], {'baseline': ['current-files']})
        baseline.assert_called_once()
        apply.assert_not_called()
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.www.iterdir()})
        self.assertFalse(self.update()['sync_pending'])
        self.assertNotIn('sync_message', self.update())
        self.assertEqual(self.update()['last_applied']['at'], self.now)

    def test_storage_error_or_active_scan_keeps_pending_receipt(self):
        self.failed_sync()
        snapshot = self.update()
        for state in ({'storage_error': 'unavailable'}, {'job': {'state': 'running'}}):
            with self.subTest(state=state), patch('router.time.time', return_value=self.now+300), \
                    patch('router.nas_request', return_value=state) as api, patch('router.apply_playlist') as apply:
                router.reconcile(self.c)
            apply.assert_not_called()
            self.assertEqual(api.call_count, 1)
            self.assertEqual(self.update(), snapshot)

    def test_disabling_auto_update_does_not_retry_or_modify_receipt(self):
        self.failed_sync()
        snapshot = self.update()
        with patch('router.nas_request') as api, patch('router.apply_playlist') as apply:
            router.reconcile({'auto_update': False})
        api.assert_not_called()
        apply.assert_not_called()
        self.assertEqual(self.update(), snapshot)

    def test_failed_final_receipt_retains_pending_and_retry_is_sync_only(self):
        self.failed_sync()
        real_write = router.atomic_json

        def write(path, data):
            if data.get('sync_pending') is False:
                raise OSError('disk full')
            real_write(path, data)

        with patch('router.time.time', return_value=self.now+300), \
                patch('router.nas_request', return_value={'rows': []}), \
                patch('router.baseline_report', return_value={'baseline': []}), \
                patch('router.atomic_json', side_effect=write), patch('router.apply_playlist') as apply:
            with self.assertRaises(OSError):
                router.reconcile(self.c)
        apply.assert_not_called()
        self.assertTrue(self.update()['sync_pending'])
        self.assertEqual(self.update()['sync_attempt_at'], self.now+300)

    def test_unchanged_and_later_failure_keep_last_success(self):
        self.failed_sync()
        previous = self.update()
        previous['sync_pending'] = False
        router.atomic_json(self.runtime / 'update.json', previous)
        for result in ('unchanged', 'error'):
            with self.subTest(result=result), patch('router.nas_request', return_value={'rows': [result]}), \
                    patch('router.apply_playlist', return_value={'state': 'unchanged'},
                          side_effect=ValueError('needs review') if result == 'error' else None):
                router.reconcile(self.c)
            self.assertEqual(self.update()['last_applied'], previous['last_applied'])
            self.assertEqual(self.update()['state'], result)


if __name__ == '__main__':
    unittest.main()
