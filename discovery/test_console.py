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
        if tag in ("script", "link", "img"):
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

    def test_glass_assets_are_local_and_help_uses_same_theme(self):
        for name in ("index.html", "wiki.html"):
            page = Elements((ASSETS / name).read_text())
            self.assertIn("glass.css", page.assets)
            self.assertIn("assets/favicon.png", page.assets)
            self.assertIn("assets/apple-touch-icon.png", page.assets)
            for asset in page.assets:
                self.assertTrue((ASSETS / asset).is_file(), asset)
        css = (ASSETS / "glass.css").read_text()
        self.assertIn('url("assets/glass-waves.webp")', css)
        self.assertNotIn("https://", css)
        self.assertIn("prefers-reduced-transparency", css)
        self.assertIn("@supports not", css)
        for name in ("glass-waves.webp", "iptv-icon.png", "iptv-mark.png", "favicon.png", "apple-touch-icon.png"):
            self.assertGreater((ASSETS / "assets" / name).stat().st_size, 100)

    def test_fixture_overview_and_discovery_use_same_channel_count(self):
        server = make_server(self.temp.name)
        self.addCleanup(server.server_close)
        self.assertEqual(len(server.worker.state["baseline"]), 36)
        self.assertEqual(server.playlists["new.m3u"].count("#EXTINF"), 36)

    def test_light_glass_transparency_preserves_dark_material(self):
        css = (ASSETS / "glass.css").read_text()
        light, dark = css.split("body.iptv-dark {", 1)
        self.assertIn("--glass: rgba(246, 249, 255, .22)", light)
        self.assertIn("--table-glass: rgba(255, 255, 255, .06)", light)
        self.assertIn("--glass: rgba(9, 19, 39, .46)", dark)
        self.assertIn("--table-glass: var(--field)", dark)

    def test_ai_fixture_uses_local_advice_and_requires_confirmation(self):
        server = make_server(self.temp.name, ai_configured=True)
        with serving(server) as port:
            def call(action, **fields):
                code, body = self.request(port, "/cgi-bin/iptv-discovery", json.dumps({"action": action, **fields}))
                return code, json.loads(body)
            code, preview = call("ai_preview", issue="playback")
            self.assertEqual(code, 200)
            self.assertTrue(preview["configured"])
            self.assertEqual(call("ai_analyze", ticket=preview["ticket"])[0], 400)
            code, result = call("ai_analyze", ticket=preview["ticket"], consent=True)
            self.assertEqual(code, 200)
            self.assertIn("本地模拟回复，未联系云端", result["advice"])
            self.assertEqual(call("ai_analyze", ticket=preview["ticket"], consent=True)[0], 400)

    def test_save_failure_preserves_original(self):
        server = make_server(self.temp.name, fail_save=True)
        with serving(server) as port:
            original = server.playlists.copy()
            code, _ = self.request(port, "/cgi-bin/iptv-action?action=save_playlist&file=new.m3u", "changed")
            self.assertEqual(code, 503)
            self.assertEqual(server.playlists, original)

    def test_ai_settings_fixture_round_trip_and_no_secret_response(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            payload = {"action": "ai_settings", "base_url": "https://api.deepseek.com/v1",
                       "model": "deepseek-v4-flash", "api_key": "local-fixture-key-not-real",
                       "save_consent": True}
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))[0], 400)
            payload["http_acknowledged"] = True
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))[0], 400)
            server.ai_secure_fixture = True
            code, body = self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))
            self.assertEqual(code, 200)
            self.assertNotIn(payload["api_key"], body)
            code, body = self.request(port, "/cgi-bin/iptv-discovery", '{"action":"ai_status"}')
            self.assertTrue(json.loads(body)["configured"])
            self.assertNotIn(payload["api_key"], body)
            self.assertFalse(any(w.get("action") == "ai_analyze" for w in server.writes))

    def test_ai_connection_fixture_never_saves_or_calls_cloud(self):
        server = make_server(self.temp.name, ai_configured=True)
        server.ai_secure_fixture = True
        with serving(server) as port:
            original = server.ai_config_path.read_bytes()
            payload = {"action": "ai_test", "test_consent": True,
                       "base_url": "https://api.deepseek.com", "model": "deepseek-flash"}
            code, result = self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(result)["ok"])
            self.assertFalse(json.loads(result)["saved"])
            self.assertEqual(server.ai_config_path.read_bytes(), original)
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", json.dumps(payload))[0], 400)

    def test_ai_password_form_and_secure_gate_contract(self):
        text = (ASSETS / "ai-diagnostics.js").read_text()
        self.assertIn('id="aiApiKey" type="password"', text)
        self.assertIn('id="aiSettingsTest"', text)
        self.assertIn("configuration?.secure_transport!==true", text)
        self.assertNotIn("http_acknowledged", text)
        self.assertNotIn("aiHttpAck", text)

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

    def test_batch_fixture_selects_known_rows_and_retains_resume_steps(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            key = next(iter(server.worker.state["observations"]))
            body = json.dumps({"action": "batch", "mode": "new", "workflow": "smart", "endpoints": [key]})
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", body)[0], 200)
            self.assertEqual(server.worker.status()["batch"]["total"], 3)
            self.assertEqual(server.worker.status()["batch"]["state"], "paused")
            resume = json.dumps({"action": "batch", "mode": "resume"})
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", resume)[0], 200)
            self.assertEqual(server.writes[-1], {"action": "batch", "mode": "resume", "targets": 3})
            self.assertEqual(server.playlists["new.m3u"].count("#EXTINF"), 36)

    def test_unsupported_production_actions_rejected(self):
        server = make_server(self.temp.name)
        with serving(server) as port:
            self.assertEqual(self.request(port, "/cgi-bin/iptv-action?action=repair_network")[0], 400)
            self.assertEqual(self.request(port, "/cgi-bin/iptv-discovery", '{"action":"scan"}')[0], 400)


if __name__ == "__main__":
    unittest.main()
