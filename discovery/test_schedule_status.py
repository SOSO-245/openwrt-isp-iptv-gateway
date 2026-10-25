"""Schedule failure tests use temporary stamps and mocked network calls."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import router


class ScheduleStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stamp = Path(temp.name) / 'schedule.json'
        self.runtime = Path(temp.name) / 'runtime'
        self.runtime.mkdir()
        for name, value in [('STAMP', self.stamp), ('RUNTIME', self.runtime)]:
            patcher = patch.object(router, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.c = {'enabled': True, 'daily_time': '09:30', 'weekdays_only': True,
                  'weekly_enabled': True, 'weekly_day': 4, 'weekly_time': '09:30', 'auto_update': True}
        self.monday = dt.datetime(2026, 10, 26, 9, 30, tzinfo=router.SHANGHAI).timestamp()
        self.friday = self.monday + 4 * 86400

    def saved(self):
        return json.loads(self.stamp.read_text())

    def test_unreachable_nas_records_failure_without_starting_capture(self):
        def fail(*_):
            self.assertEqual(self.saved()['day'], '2026-10-26')
            raise ConnectionRefusedError('private upstream detail')
        with patch('router.link_status', return_value={}), patch('router.nas_request', side_effect=fail), patch('router.sample') as sample:
            router.schedule_tick(self.c, self.monday)
        sample.assert_not_called()
        self.assertEqual(self.saved()['runs']['sample']['state'], 'error')
        self.assertNotIn('private upstream detail', self.stamp.read_text())

    def test_failure_is_not_retried_in_same_minute_or_after_restart(self):
        with patch('router.link_status', return_value={}), patch('router.nas_request', side_effect=OSError), patch('router.sample') as sample:
            router.schedule_tick(self.c, self.monday)
        with patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}), patch('router.sample') as sample:
            router.schedule_tick(self.c, self.monday + 20)
            sample.assert_not_called()
        self.assertEqual(self.saved()['runs']['sample']['state'], 'error')

    def test_friday_submission_is_not_reported_as_completed(self):
        with patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}) as api, patch('router.baseline_report', return_value={}), patch('router.sample') as sample:
            router.schedule_tick(self.c, self.friday)
        self.assertEqual(self.saved()['day'], '2026-10-30')
        self.assertEqual(self.saved()['weekly'], '2026-10-30')
        self.assertEqual(self.saved()['runs']['audit']['state'], 'submitted')
        self.assertNotIn('sample', self.saved()['runs'])
        self.assertEqual(api.call_args.args[2], {'trigger': 'scheduled'})
        sample.assert_not_called()

    def test_lost_submission_response_stays_uncertain(self):
        def request(_config, path, _data):
            if path == '/v1/audit':
                raise TimeoutError('response lost')
            return {}
        with patch('router.link_status', return_value={}), patch('router.nas_request', side_effect=request), patch('router.baseline_report', return_value={}):
            router.schedule_tick(self.c, self.friday)
        self.assertEqual(self.saved()['runs']['audit']['state'], 'unconfirmed')
        with patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}) as api:
            router.schedule_tick(self.c, self.friday + 20)
        self.assertEqual([call.args[1] for call in api.call_args_list], ['/v1/link'])

    def test_sample_failure_not_reported_as_success(self):
        with patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}), patch('router.sample', return_value={'state': 'error'}):
            router.schedule_tick(self.c, self.monday)
        self.assertEqual(self.saved()['runs']['sample']['state'], 'error')

    def test_zero_observations_is_a_completed_sample(self):
        with patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}), patch('router.sample', return_value={'state': 'complete', 'count': 0}):
            router.schedule_tick(self.c, self.monday)
        self.assertEqual(self.saved()['runs']['sample']['state'], 'complete')
        self.assertEqual(self.saved()['runs']['sample']['count'], 0)

    def test_stamp_failure_prevents_any_network_side_effect(self):
        with patch('router.atomic_json', side_effect=OSError), patch('router.nas_request') as api, patch('router.sample') as sample:
            with self.assertRaises(OSError):
                router.schedule_tick(self.c, self.friday)
        api.assert_not_called()
        sample.assert_not_called()

    def test_final_stamp_failure_preserves_claim_and_unknown_outcome(self):
        real_write = router.atomic_json
        def write(path, data):
            if self.stamp.exists():
                raise OSError('disk full')
            real_write(path, data)
        with patch('router.atomic_json', side_effect=write), patch('router.link_status', return_value={}), patch('router.nas_request', return_value={}), patch('router.sample', return_value={'state': 'complete', 'count': 0}):
            with self.assertRaises(OSError):
                router.schedule_tick(self.c, self.monday)
        self.assertEqual(self.saved()['day'], '2026-10-26')
        self.assertEqual(self.saved()['runs']['sample']['state'], 'unconfirmed')

    def test_status_exposes_only_local_information_when_nas_is_down(self):
        router.atomic_json(self.stamp, {'day': '2026-10-26', 'runs': {'sample': {'state': 'error', 'at': self.monday}}})
        with patch('router.nas_request', side_effect=OSError('sensitive connection details')), patch('router.time.time', return_value=self.monday + 120):
            data = router.dashboard_status(self.c)
        self.assertIn('service_error', data)
        self.assertNotIn('sensitive connection details', str(data))
        self.assertNotIn('baseline_count', data)
        self.assertNotIn('rows', data)
        self.assertEqual(data['collector']['daily_time'], '09:30')
        self.assertEqual(data['scheduled_runs']['sample']['state'], 'error')
        self.assertEqual(data['next_runs']['sample'], self.monday + 86400)
        with patch('router.nas_request', return_value={'baseline_count': 2, 'rows': []}):
            restored = router.dashboard_status(self.c)
        self.assertNotIn('service_error', restored)
        self.assertEqual(restored['baseline_count'], 2)


if __name__ == '__main__':
    unittest.main()
