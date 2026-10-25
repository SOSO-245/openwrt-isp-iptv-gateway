"""Router-side coordination uses temporary files and mocked NAS calls only."""
import io
import json
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router


class BaselineCoordinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.runtime = self.root / 'runtime'
        self.www = self.root / 'www'
        self.www.mkdir()
        self.old = '#EXTM3U\n#EXTINF:-1,Example A\nhttp://192.0.2.1:12123/udp/239.255.42.1:8000\n'
        self.new = self.old + '#EXTINF:-1,Example B\nhttp://192.0.2.1:12123/udp/239.255.42.2:8000\n'
        self.set_playlist(self.old)
        self.config = {'auto_update': True, 'enabled': True, 'daily_time': '09:30',
                       'weekdays_only': True, 'weekly_enabled': True, 'weekly_day': 4, 'weekly_time': '09:30'}
        for name, value in [('RUNTIME', self.runtime), ('WWW', self.www), ('STAMP', self.root / 'schedule.json')]:
            patcher = patch.object(router, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in [('config', self.config), ('catalog', {'rows': [], 'at': 0}),
                            ('capture_interface', 'synthetic0'), ('legacy_busy', False),
                            ('link_status', {'ready': True, 'generation': 'synthetic'})]:
            patcher = patch.object(router, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def set_playlist(self, text):
        for name in ('new.m3u', 'new-logo.m3u'):
            (self.www / name).write_text(text)

    def test_sample_publishes_latest_playlist_not_pre_capture_snapshot(self):
        def capture(_interface):
            self.set_playlist(self.new)
            return []

        with patch('router.capture', side_effect=capture), patch('router.nas_request', return_value={}) as api:
            self.assertEqual(router.sample()['state'], 'complete')
        report = api.call_args.args[2]
        self.assertEqual(len(report['baseline']), 2)
        self.assertEqual(report['trigger'], 'manual')

    def test_capture_does_not_hold_playlist_lock_but_publication_does(self):
        def capture(_interface):
            with router.playlist_guard():
                pass
            return []

        def request(*_):
            with self.assertRaisesRegex(ValueError, '正在更新'):
                with router.playlist_guard():
                    self.fail('publication lock was not held')
            return {}

        with patch('router.capture', side_effect=capture), patch('router.nas_request', side_effect=request):
            self.assertEqual(router.sample()['state'], 'complete')
        with router.playlist_guard():
            pass

    def test_busy_update_does_not_publish_old_sample_or_wait_indefinitely(self):
        with router.playlist_guard(), patch('router.capture', return_value=[]), patch('router.nas_request') as api:
            result = router.sample()
        api.assert_not_called()
        self.assertEqual(result['state'], 'error')
        self.assertIn('稍后重试', result['error'])

    def test_empty_playlist_after_capture_is_not_sent_as_baseline(self):
        def capture(_interface):
            self.set_playlist('#EXTM3U\n')
            return []
        with patch('router.capture', side_effect=capture), patch('router.nas_request') as api:
            result = router.sample()
        api.assert_not_called()
        self.assertEqual(result['state'], 'error')

    def test_reconcile_cannot_apply_during_snapshot_acknowledgement(self):
        def request(*_):
            router.reconcile(self.config)
            return {}
        with patch('router.capture', return_value=[]), patch('router.nas_request', side_effect=request), \
                patch('router.apply_playlist') as apply:
            self.assertEqual(router.sample()['state'], 'complete')
        apply.assert_not_called()

    def test_failed_publication_releases_guard(self):
        with patch('router.capture', return_value=[]), patch('router.nas_request', side_effect=TimeoutError):
            self.assertEqual(router.sample()['state'], 'error')
        with router.playlist_guard():
            pass

    def test_all_manual_checks_sync_before_launch_with_lock_held(self):
        for action in ('scan', 'verify', 'audit', 'identify'):
            with self.subTest(action=action):
                calls = []
                def request(_config, path, payload):
                    calls.append(path)
                    with self.assertRaises(ValueError):
                        with router.playlist_guard():
                            pass
                    return {}
                with patch('router.nas_request', side_effect=request):
                    router.submit_check(self.config, action, {'endpoints': []})
                self.assertEqual(calls, ['/v1/link', '/v1/sample', '/v1/' + action])

    def test_baseline_failure_prevents_check_launch_and_frees_lock(self):
        def request(_config, path, payload):
            if path == '/v1/sample':
                raise TimeoutError('simulated missing ack')
            return {}
        with patch('router.nas_request', side_effect=request) as api, self.assertRaises(TimeoutError):
            router.submit_check(self.config, 'audit', {})
        self.assertNotIn('/v1/audit', [c.args[1] for c in api.call_args_list])
        with router.playlist_guard():
            pass

    def test_weekly_schedule_locks_baseline_and_submission_together(self):
        friday = dt.datetime(2026, 10, 30, 9, 30, tzinfo=router.SHANGHAI).timestamp()
        def request(_config, path, payload):
            if path != '/v1/link':
                with self.assertRaises(ValueError):
                    with router.playlist_guard():
                        pass
            return {}
        with patch('router.nas_request', side_effect=request):
            router.schedule_tick(self.config, friday)
        self.assertEqual(json.loads(router.STAMP.read_text())['runs']['audit']['state'], 'submitted')

    def invoke_cgi(self, action):
        raw = json.dumps({'action': action, 'user': 'test', 'password': 'test-only'}).encode()
        output = io.StringIO()
        read_text = Path.read_text
        def read(path, *args, **kwargs):
            return 'test:test-only' if path == Path('/etc/soso-iptv-auth') else read_text(path, *args, **kwargs)
        with patch.dict(os.environ, {'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': 'application/json',
                                     'CONTENT_LENGTH': str(len(raw))}, clear=True), \
                patch('router.Path.read_text', autospec=True, side_effect=read), \
                patch('router.sys.stdin') as stdin, patch('router.sys.stdout', output):
            stdin.buffer = io.BytesIO(raw)
            router.cgi()
        return output.getvalue()

    def test_busy_cgi_writes_fail_without_side_effects_but_pause_and_status_work(self):
        with router.playlist_guard():
            for action in ('scan', 'verify', 'audit', 'identify', 'name', 'catalog'):
                with self.subTest(action=action), patch('router.nas_request') as api:
                    response = self.invoke_cgi(action)
                self.assertIn('Status: 400', response)
                self.assertIn('稍后重试', response)
                api.assert_not_called()
            for action in ('pause', 'status'):
                with self.subTest(action=action), patch('router.nas_request', return_value={}) as api:
                    response = self.invoke_cgi(action)
                self.assertIn('Status: 200', response)
                self.assertEqual(api.call_args.args[1], '/v1/' + action)

    def test_catalog_and_confirmation_sync_with_lock_held(self):
        for action in ('catalog', 'name'):
            calls = []
            def request(_config, path, payload):
                calls.append(path)
                with self.assertRaises(ValueError):
                    with router.playlist_guard():
                        pass
                return {}
            with self.subTest(action=action), patch('router.nas_request', side_effect=request):
                self.assertIn('Status: 200', self.invoke_cgi(action))
            self.assertEqual(calls, ['/v1/sample', '/v1/name'] if action == 'name' else ['/v1/sample'])


if __name__ == '__main__':
    unittest.main()
