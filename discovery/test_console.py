"""Console contracts use loopback and temporary state, never household devices."""
import http.client
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from console_fixture import make_server
from test_release import serving
from ui_fixture import ASSETS


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.assets, self.views = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag in ("script", "link"):
            url = attrs.get("src", attrs.get("href", ""))
            if url.startswith("/iptv/"):
                self.assets.append(url.split("?", 1)[0].removeprefix("/iptv/"))
        if tag == "section" and "data-tab" in attrs:
            self.views.append(attrs["data-tab"])


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def request(self, port, path, body=""):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            conn.request("POST", path, body.encode(), {"Content-Type": "application/json"})
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def test_six_views_unique_ids_and_all_assets_present(self):
        page = Elements((ASSETS / "index.html").read_text())
        self.assertEqual(page.views, ["overview", "playlist", "capture", "epg", "automation", "maintenance"])
        self.assertEqual(len(page.ids), len(set(page.ids)))
        for asset in page.assets:
            self.assertTrue((ASSETS / asset).is_file(), asset)

    def test_fixture_save_and_restore_are_in_memory(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            original = server.playlists["new.m3u"]
            new = original.replace("demo0", "changed-id", 1)
            self.assertEqual(self.request(port, "/cgi-bin/iptv-action?action=save_playlist&file=new.m3u", new)[0], 200)
            self.assertEqual(self.request(port, "/cgi-bin/iptv-action?action=get_playlist&file=new.m3u")[1], new)
            self.request(port, "/cgi-bin/iptv-action?action=restore_history&file=new.m3u&bak=demo-backup")
            self.assertEqual(server.playlists["new.m3u"], original)

    def test_fixture_overview_and_discovery_use_same_channel_count(self):
        server = make_server(self.temp.name)
        self.addCleanup(server.server_close)
        self.assertEqual(len(server.worker.state["baseline"]), 36)
        self.assertEqual(server.playlists["new.m3u"].count("#EXTINF"), 36)

    def test_save_failure_preserves_original(self):
        server = make_server(self.temp.name, fail_save=True)
        with serving(server) as port:
            original = server.playlists.copy()
            code, _ = self.request(port, "/cgi-bin/iptv-action?action=save_playlist&file=new.m3u", "changed")
            self.assertEqual(code, 503)
            self.assertEqual(server.playlists, original)

    def test_settings_round_trip_and_offline_guard(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            payload = {"action": "settings", "settings": {"enabled": True, "daily_time": "10:30", "active_enabled": False, "ranges": [], "ports": []}}
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))[0], 200)
            code, data = self.request(port, "/cgi-bin/iptv-discovery", '{"action":"status"}')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(data)["collector"]["daily_time"], "10:30")
            server.offline = True
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))[0], 400)

    def test_unsupported_production_actions_rejected(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            self.assertEqual(self.request(port, "/cgi-bin/iptv-action?action=repair_network")[0], 400)
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", '{"action":"scan"}')[0], 400)


if __name__ == "__main__":
    unittest.main()
