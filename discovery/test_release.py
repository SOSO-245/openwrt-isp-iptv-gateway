"""Release checks use synthetic media, loopback HTTP and temporary files only."""
import contextlib
import http.client
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from common import media_fingerprint
from playlist_update import apply, parse
from server import BoundedServer, Handler, Worker, media_details

ROOT = Path(__file__).resolve().parents[1]
CONNECTION = http.client.HTTPConnection


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Worker(Path(self.temp.name) / 'state.json', '192.0.2.1')

    def api(self, port, path, payload=None, token=None):
        connection = CONNECTION('127.0.0.1', port, timeout=5)
        try:
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['Authorization'] = 'Bearer ' + token
            connection.request('POST' if payload is not None else 'GET', path,
                               json.dumps(payload) if payload is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def make_server(self):
        server = BoundedServer(('127.0.0.1', 0), Handler)
        server.worker = self.worker
        server.token = secrets.token_urlsafe(32)
        return server

    def test_api_confirmation_persistence_and_playlist_transaction(self):
        key = '239.255.42.2:8000'
        base = [{'endpoint': '239.255.42.1:8000', 'name': 'CCTV-1', 'protocol': 'udp'}]
        result = {'validation': 'media', 'protocol': 'udp', 'programs': [],
                  'streams': [{'type': 'video', 'codec': 'h264'}]}
        server = self.make_server()
        with serving(server) as port:
            self.assertEqual(self.api(port, '/v1/status')[0], 403)
            self.assertEqual(self.api(port, '/v1/sample', {'baseline': base}, 'wrong')[0], 403)
            self.assertEqual(self.api(port, '/health')[0], 200)
            self.assertEqual(self.api(port, '/v1/sample', {'baseline': base}, server.token)[0], 200)
            now = time.time()
            with patch('server.time.time', return_value=now - 61):
                self.worker.observe(key, result)
            self.worker.observe(key, result)
            payload = {'endpoint': key, 'name': 'CCTV-2', 'reviewed': True,
                       'fingerprint': media_fingerprint(key, self.worker.state['observations'][key])}
            self.assertEqual(self.api(port, '/v1/name', dict(payload, reviewed=False), server.token)[0], 400)
            self.assertEqual(self.api(port, '/v1/name', payload, server.token)[0], 200)
            status, data = self.api(port, '/v1/status', token=server.token)
            self.assertEqual(status, 200)
            self.assertEqual(data['rows'][0]['name_source'], 'manual')
            self.assertEqual(data['rows'][0]['verified_count'], 2)
        restored = Worker(self.worker.path, self.worker.router)
        www = Path(self.temp.name) / 'www'
        www.mkdir()
        base_text = '#EXTM3U\n#EXTINF:-1 group-title="kept",CCTV-1\nhttp://router.example:12123/udp/239.255.42.1:8000\n'
        for name in ('new.m3u', 'new-logo.m3u'):
            (www / name).write_text(base_text)
        (www / 'new.txt').write_text('original text\n')
        result = apply(www, Path(self.temp.name) / 'backups', restored.rows())
        self.assertEqual(result['state'], 'applied')
        self.assertEqual([r['name'] for r in parse((www / 'new.m3u').read_text())[1]], ['CCTV-1', 'CCTV-2'])
        self.assertEqual((Path(result['backup']) / 'new.m3u').read_text(), base_text)
        self.assertEqual((www / 'new.m3u').read_text(), (www / 'new-logo.m3u').read_text())
        self.assertEqual(apply(www, Path(self.temp.name) / 'backups', restored.rows())['state'], 'unchanged')

    def test_storage_failure_api_and_health_recover_without_restart(self):
        server = self.make_server()
        payload = {'baseline': [{'endpoint': '239.255.42.1:8000', 'name': 'Example', 'protocol': 'udp'}]}
        with serving(server) as port:
            with patch('server.atomic_json', side_effect=OSError('disk full')):
                code, result = self.api(port, '/v1/sample', payload, server.token)
            self.assertEqual(code, 503)
            self.assertIn('error', result)
            self.assertNotIn('disk full', str(result))
            self.assertEqual(self.api(port, '/health'), (503, {'status': 'storage_error'}))
            code, status = self.api(port, '/v1/status', token=server.token)
            self.assertEqual(code, 200)
            self.assertEqual(status['baseline_count'], 0)
            self.assertTrue(status['storage_error'])
            self.assertEqual(self.api(port, '/v1/sample', payload, server.token)[0], 200)
            self.assertEqual(self.api(port, '/health'), (200, {'status': 'ok'}))
            self.assertEqual(self.api(port, '/v1/status', token=server.token)[1]['baseline_count'], 1)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and FFprobe required')
    def test_real_media_http_audit_and_worker_restart(self):
        media = subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
            '-i', 'testsrc=size=160x90:rate=10', '-t', '1', '-c:v', 'mpeg2video',
            '-metadata', 'service_name=Example Channel', '-f', 'mpegts', 'pipe:1',
        ], check=True, capture_output=True, timeout=15).stdout
        self.assertEqual(media_details(media)['validation'], 'media')
        paths = []

        class StreamHandler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                paths.append(self.path)
                body = (b'<table><tr><td>Active clients</td></tr><tr><td>0</td></tr></table>'
                        if self.path == '/status/' else media)
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with serving(ThreadingHTTPServer(('127.0.0.1', 0), StreamHandler)) as stream_port:
            def connection(host, port, **kwargs):
                self.assertEqual((host, port), ('192.0.2.1', 12123))
                return CONNECTION('127.0.0.1', stream_port, **kwargs)

            self.worker.ingest({'baseline': [
                {'endpoint': f'239.255.42.{n}:8000', 'protocol': 'udp', 'name': f'Example {n}'}
                for n in range(1, 4)], 'observations': []})
            self.worker.set_link({'ready': True, 'generation': 'synthetic-link'})
            server = self.make_server()
            with serving(server) as port, patch('server.http.client.HTTPConnection', side_effect=connection), patch.object(self.worker.cancel, 'wait', return_value=False):
                code, result = self.api(port, '/v1/audit', {}, server.token)
                self.assertEqual((code, result['count']), (200, 3))
                deadline = time.monotonic() + 15
                while self.worker.job_lock.locked() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertFalse(self.worker.job_lock.locked())
                status = self.api(port, '/v1/status', token=server.token)[1]
                self.assertEqual(status['job']['state'], 'complete')
                self.assertEqual(status['audit']['counts'], {'media': 3})
                self.assertEqual(status['audit']['done'], 3)
                self.assertTrue(all(row['streams'][0]['codec'] == 'mpeg2video' for row in status['rows']))
            self.assertEqual(len([path for path in paths if path.startswith('/udp/')]), 3)
        restored = Worker(self.worker.path, self.worker.router)
        self.assertEqual(restored.state['audit']['state'], 'complete')
        self.assertEqual(len(restored.rows()), 3)


class PublicGatewayTests(unittest.TestCase):
    def test_no_guessed_gateway_before_repair(self):
        paths = ['openwrt/data/www/cgi-bin/iptv-action',
                 'docker/data/www/cgi-bin/iptv-action', 'docker/rootfs/www/cgi-bin/iptv-action']
        for path in paths:
            text = (ROOT / path).read_text()
            fn = re.search(r'^current_lan_gateway\(\)\{\n.*?^\}', text, re.M | re.S)[0]
            repair = re.search(r'^repair_network\(\)\{\n.*?^\}', text, re.M | re.S)[0]
            result = subprocess.run(['sh', '-c', 'ip(){ return 0; }; uci(){ return 1; };\n' + fn + '\ncurrent_lan_gateway'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b'')
            result = subprocess.run(['sh', '-c', 'ip(){ return 0; }; uci(){ echo 192.0.2.254; };\n' + fn + '\ncurrent_lan_gateway'], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), b'192.0.2.254')
            result = subprocess.run(['sh', '-c', 'current_lan_gateway(){ return 1; }; uci(){ echo MUTATED; };\n' + repair + '\nrepair_network'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(b'MUTATED', result.stdout)


if __name__ == '__main__':
    unittest.main()
