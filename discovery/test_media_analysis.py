"""Bounded parser checks; all media and transport responses are synthetic."""
import copy
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from server import Worker, media_details


VIDEO = {'codec_type': 'video', 'codec_name': 'h264', 'width': 320, 'height': 180}
AUDIO = {'codec_type': 'audio', 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2}
PROGRAM = {'program_id': 1, 'tags': {'service_name': 'Example Channel'}}


class MediaAnalysisTests(unittest.TestCase):
    def analyze(self, streams, **kwargs):
        payload = {'streams': streams, 'programs': [PROGRAM]}
        proc = subprocess.CompletedProcess([], 0, json.dumps(payload).encode(), b'')
        with patch('server.subprocess.run', return_value=proc):
            return media_details(b'synthetic', **kwargs)

    def test_complete_audio_video(self):
        result = self.analyze([VIDEO, AUDIO])
        self.assertEqual(result['validation'], 'media')
        self.assertEqual(result['analysis'], {'reason': 'audio_video', 'video_streams': 1,
                                             'audio_streams': 1, 'incomplete_streams': 0})
        self.assertEqual(result['programs'][0]['name'], 'Example Channel')

    def test_valid_radio_is_not_rejected_for_having_no_video(self):
        result = self.analyze([AUDIO])
        self.assertEqual(result['validation'], 'media')
        self.assertEqual(result['analysis']['reason'], 'audio_only')

    def test_valid_video_only(self):
        result = self.analyze([VIDEO])
        self.assertEqual(result['validation'], 'media')
        self.assertEqual(result['analysis']['reason'], 'video_only')

    def test_unknown_video_parameters_are_not_success_even_with_valid_audio(self):
        for width in (0, None, -1, True, '320'):
            with self.subTest(width=width):
                result = self.analyze([{**VIDEO, 'width': width}, AUDIO])
                self.assertEqual(result['validation'], 'inconclusive')
                self.assertEqual(result['analysis']['reason'], 'stream_parameters_incomplete')
                self.assertEqual(result['analysis']['incomplete_streams'], 1)
                self.assertEqual(result['analysis']['video_streams'], 0)

    def test_guessed_audio_codec_without_parameters_is_not_media(self):
        for rate, channels in (('0', 0), ('48000', 0), ('unknown', 2), (True, 2), ('48000', True)):
            with self.subTest(rate=rate, channels=channels):
                result = self.analyze([{**AUDIO, 'codec_name': 'mp3',
                                        'sample_rate': rate, 'channels': channels}])
                self.assertEqual(result['validation'], 'inconclusive')
                self.assertEqual(result['streams'], [])

    def test_unknown_codec_does_not_certify_a_partial_program(self):
        result = self.analyze([VIDEO, {**AUDIO, 'codec_name': 'unknown'}])
        self.assertEqual(result['validation'], 'inconclusive')

    def test_data_or_subtitles_do_not_count_as_audio_video(self):
        result = self.analyze([{'codec_type': 'subtitle', 'codec_name': 'dvb_subtitle'}])
        self.assertEqual(result['validation'], 'no_media')
        self.assertEqual(result['analysis']['reason'], 'no_av_streams')

    def test_parser_failure_is_not_channel_silence(self):
        proc = subprocess.CompletedProcess([], 1, b'{}', b'synthetic-private-error')
        with patch('server.subprocess.run', return_value=proc):
            result = media_details(b'partial')
        self.assertEqual(result['validation'], 'inconclusive')
        self.assertEqual(result['analysis']['reason'], 'parser_failed')
        self.assertNotIn('synthetic-private-error', json.dumps(result))

    def test_parser_timeout_and_unavailable_are_fixed_diagnostics(self):
        cases = [(subprocess.TimeoutExpired('ffprobe', 3), 'parser_timeout'),
                 (FileNotFoundError('private-path'), 'parser_unavailable')]
        for error, reason in cases:
            with self.subTest(reason=reason), patch('server.subprocess.run', side_effect=error):
                result = media_details(b'partial')
                self.assertEqual(result['validation'], 'inconclusive')
                self.assertEqual(result['analysis']['reason'], reason)
                self.assertNotIn('private-path', json.dumps(result))

    def test_malformed_or_oversized_output_is_not_media(self):
        payloads = [b'not-json', b'[]', b'{"streams":null}',
                    b'{"streams":[null]}', b'{"programs":[{"tags":[]}]}']
        for raw in payloads + [b' ' * 262145]:
            with self.subTest(raw=raw[:40]):
                proc = subprocess.CompletedProcess([], 0, raw, b'')
                with patch('server.subprocess.run', return_value=proc):
                    result = media_details(b'partial')
                self.assertEqual(result['validation'], 'inconclusive')
                expected = 'output_limit' if len(raw) > 262144 else 'invalid_output'
                self.assertEqual(result['analysis']['reason'], expected)

    def test_stream_limit_cannot_hide_an_incomplete_stream(self):
        result = self.analyze([AUDIO] * 32 + [{**VIDEO, 'width': 0}])
        self.assertEqual(result['validation'], 'inconclusive')
        self.assertEqual(result['analysis']['reason'], 'output_limit')

    def test_existing_parser_resource_limits_remain(self):
        for deep, budget, size, duration in ((False, 3, '1048576', '1500000'),
                                             (True, 5, '8388608', '8000000')):
            proc = subprocess.CompletedProcess([], 0, b'{}', b'')
            with patch('server.subprocess.run', return_value=proc) as run:
                media_details(b'partial', deep=deep)
            args = run.call_args.args[0]
            self.assertEqual(run.call_args.kwargs['timeout'], budget)
            self.assertEqual(args[args.index('-probesize') + 1], size)
            self.assertEqual(args[args.index('-analyzeduration') + 1], duration)
            self.assertEqual(args[args.index('-protocol_whitelist') + 1], 'pipe')

    def test_probe_keeps_transport_and_parser_diagnostics_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(Path(tmp) / 'state.json', '192.0.2.1')
            conn, response = Mock(), io.BytesIO(b'partial')
            response.status = 200
            conn.getresponse.return_value = response
            with patch('server.http.client.HTTPConnection', return_value=conn), \
                    patch('server.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'{}', b'')):
                result = worker.probe('239.255.42.1:8000')
            self.assertEqual(result['validation'], 'inconclusive')
            self.assertEqual(result['last_probe']['stage'], 'body')
            self.assertEqual(result['last_probe']['reason'], 'eof')
            self.assertEqual(result['last_probe']['analysis']['reason'], 'parser_failed')
            self.assertNotIn('analysis', result)

    def test_incomplete_media_breaks_confirmation_but_preserves_last_known_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(Path(tmp) / 'state.json', '192.0.2.1')
            key = '239.255.42.1:8000'
            good = self.analyze([VIDEO, AUDIO])
            worker.observe(key, good)
            old = copy.deepcopy(worker.state['observations'][key]['streams'])
            partial = self.analyze([{**VIDEO, 'width': 0}, AUDIO])
            worker.observe(key, partial)
            row = worker.state['observations'][key]
            self.assertEqual(row['last_check'], 'inconclusive')
            self.assertEqual(row['success_checks'], [])
            self.assertEqual(row['daytime_failure_checks'], [])
            self.assertEqual(row['archive_failure_checks'], [])
            self.assertEqual(row['streams'], old)


if __name__ == '__main__':
    unittest.main()
