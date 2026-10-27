"""AI privacy, consent and failure checks. Never use real keys or cloud requests."""
import fcntl
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ai_diagnostics as ai
from common import atomic_json
import router


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ai.json"
        self.runtime = Path(self.temp.name) / "runtime"
        self.cfg = {"base_url": ai.BASE_URL, "model": ai.MODEL, "api_key": "test-only-not-a-real-key"}
        atomic_json(self.path, self.cfg)
        self.data = {"baseline_count": 12, "rows": [
            {"name": "PRIVATE-NAME", "endpoint": "239.255.42.1:8000", "last_check": "no_response", "change": "existing"},
            {"name_source": "unknown", "change": "new"}],
            "audit": {"state": "paused", "done": 4, "total": 12, "message": "PRIVATE-LOG", "counts": {"media": 3, "no_response": 1}},
            "capture": {"state": "complete", "count": 0}, "secret": "DO-NOT-SEND",
            "collector": {"token": "DO-NOT-SEND"}, "scheduled_runs": {"audit": {"state": "submitted"}}}

    def handle(self, action, **fields):
        secure_transport = fields.pop("secure_transport", True)
        return ai.handle(action, fields, "test-user", lambda: self.data,
                         lambda: {"ready": True, "generation": "192.0.2.1:1234"}, self.path, self.runtime,
                         secure_transport=secure_transport)

    def preview(self):
        return self.handle("ai_preview", issue="playback")

    def save(self, **fields):
        return self.handle("ai_settings", **{
            "save_consent": True,
            "base_url": ai.BASE_URL, "model": ai.MODEL, **fields})

    def test_browser_settings_first_save_is_private_and_never_calls_cloud(self):
        self.path.unlink()
        with patch.object(ai, "ask_cloud") as cloud:
            result = self.save(api_key=self.cfg["api_key"], base_url=ai.BASE_URL + "/v1/")
            self.assertTrue(result["configured"])
            self.assertNotIn(self.cfg["api_key"], json.dumps(result))
            self.assertEqual(ai.settings(self.path), self.cfg)
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            cloud.assert_not_called()

    def test_browser_settings_blank_preserves_key_and_invalidates_preview(self):
        self.preview()
        result = self.save(api_key="", model="deepseek-flash")
        self.assertEqual(result["model"], "deepseek-flash")
        self.assertEqual(ai.settings(self.path)["api_key"], self.cfg["api_key"])
        self.assertFalse((self.runtime / "preview.json").exists())
        replacement = "fixture-replacement-not-a-real-key"
        self.save(api_key=replacement)
        self.assertEqual(ai.settings(self.path)["api_key"], replacement)

    def test_browser_settings_consent_and_https_required(self):
        original = self.path.read_bytes()
        for fields in ({"save_consent": False}, {"secure_transport": False, "http_acknowledged": True}):
            with self.assertRaises(PermissionError):
                self.save(**fields)
            self.assertEqual(self.path.read_bytes(), original)
        result = ai.handle("ai_settings", {"save_consent": True, "base_url": ai.BASE_URL,
                           "model": ai.MODEL}, "test-user", lambda: {}, lambda: {},
                           self.path, self.runtime, secure_transport=True)
        self.assertTrue(result["secure_transport"])

    def test_browser_settings_invalid_input_never_overwrites(self):
        original = self.path.read_bytes()
        for fields in ({"api_key": "bad key"}, {"api_key": 9}, {"api_key": "x" * 513},
                       {"base_url": "http://api.deepseek.com"}, {"base_url": "https://api.deepseek.com.evil.example"},
                       {"base_url": "https://api.deepseek.com/?secret=PRIVATE"}, {"model": "arbitrary"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError) as error:
                self.save(**fields)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertEqual(self.path.read_bytes(), original)
        self.path.unlink()
        with self.assertRaises(ValueError):
            self.save(api_key="")
        self.assertFalse(self.path.exists())

    def test_browser_settings_reject_unsafe_existing_file_and_storage_failure(self):
        original = self.path.read_bytes()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.save(api_key=self.cfg["api_key"])
        self.path.chmod(0o600)
        with patch.object(ai, "atomic_json", side_effect=OSError("PRIVATE-storage-error")):
            with self.assertRaises(ValueError) as error:
                self.save(api_key=self.cfg["api_key"])
            self.assertNotIn("PRIVATE", str(error.exception))
        self.assertEqual(self.path.read_bytes(), original)
        target = self.path.with_name("target.json")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.save(api_key=self.cfg["api_key"])
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), original)

    def test_browser_settings_do_not_load_network_summary_and_share_lock(self):
        self.runtime.mkdir()
        with (self.runtime / "request.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                self.save()
        load = Mock(side_effect=AssertionError("must not query devices"))
        ai.handle("ai_settings", {"save_consent": True,
                  "base_url": ai.BASE_URL, "model": ai.MODEL}, "test-user", load, load,
                  self.path, self.runtime, secure_transport=True)
        load.assert_not_called()

    def test_connection_uses_draft_without_saving_or_querying_home(self):
        original = self.path.read_bytes()
        load = Mock(side_effect=AssertionError("must not query home"))
        request = {"test_consent": True, "base_url": ai.BASE_URL, "model": "deepseek-flash"}
        with patch.object(ai, "test_connection", return_value={"ok": True}) as cloud:
            result = ai.handle("ai_test", request, "test-user", load, load, self.path, self.runtime, secure_transport=True)
            self.assertTrue(result["ok"])
            self.assertFalse(result["saved"])
            self.assertEqual(cloud.call_args.args[0]["api_key"], self.cfg["api_key"])
            self.assertEqual(cloud.call_args.args[0]["model"], "deepseek-flash")
        self.assertEqual(self.path.read_bytes(), original)
        load.assert_not_called()

    def test_connection_requires_https_consent_and_rate_limit(self):
        request = {"test_consent": True, "base_url": ai.BASE_URL, "model": ai.MODEL}
        with patch.object(ai, "test_connection", return_value={"ok": True}) as cloud:
            for fields in ({"test_consent": False}, {"secure_transport": False, "http_acknowledged": True}):
                with self.assertRaises(PermissionError):
                    self.handle("ai_test", **{**request, **fields})
            cloud.assert_not_called()
            self.handle("ai_test", **request)
            with self.assertRaisesRegex(ValueError, "15秒"):
                self.handle("ai_test", **request)
            cloud.assert_called_once()

    def test_connection_only_uses_models_get_and_filters_remote_names(self):
        with patch.object(ai.http.client, "HTTPSConnection") as ctor:
            conn = ctor.return_value
            conn.getresponse.return_value.status = 200
            conn.getresponse.return_value.read.return_value = json.dumps({"data": [{"id": ai.MODEL}, {"id": "PRIVATE-REMOTE"}]}).encode()
            result = ai.test_connection(self.cfg)
            self.assertTrue(result["ok"])
            self.assertEqual(result["available_models"], [ai.MODEL])
            self.assertNotIn("PRIVATE", json.dumps(result))
            ctor.assert_called_once_with("api.deepseek.com", timeout=8)
            self.assertEqual(conn.request.call_args.args, ("GET", "/models"))
            self.assertNotIn("body", conn.request.call_args.kwargs)
            conn.close.assert_called_once()

    def test_connection_model_not_listed_is_not_a_success(self):
        with patch.object(ai.http.client, "HTTPSConnection") as ctor:
            ctor.return_value.getresponse.return_value.status = 200
            ctor.return_value.getresponse.return_value.read.return_value = b'{"data":[{"id":"deepseek-flash"}]}'
            result = ai.test_connection(self.cfg)
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "model_not_listed")
            self.assertEqual(result["available_models"], ["deepseek-flash"])

    def test_connection_http_errors_never_echo_key_or_response(self):
        for status, code in ((301, "service_unavailable"), (401, "authentication_failed"),
                             (402, "balance_required"), (403, "access_denied"), (429, "rate_limited"), (500, "service_unavailable")):
            with self.subTest(status=status), patch.object(ai.http.client, "HTTPSConnection") as ctor:
                conn = ctor.return_value
                conn.getresponse.return_value.status = status
                result = ai.test_connection(self.cfg)
                self.assertEqual(result["code"], code)
                self.assertFalse(result["ok"])
                self.assertNotIn(self.cfg["api_key"], json.dumps(result))
                conn.getresponse.return_value.read.assert_not_called()
                conn.request.assert_called_once()

    def test_connection_network_and_invalid_responses_are_bounded(self):
        for error, code in ((TimeoutError(), "timeout"), (OSError("PRIVATE"), "connection_failed"),
                            (ai.ssl.SSLError("PRIVATE"), "tls_failed")):
            with patch.object(ai.http.client, "HTTPSConnection") as ctor:
                ctor.return_value.request.side_effect = error
                result = ai.test_connection(self.cfg)
                self.assertEqual(result["code"], code)
                self.assertNotIn("PRIVATE", json.dumps(result))
        for raw in (b"invalid", b"[]", b"{}", b'{"data":{}}', b'{"data":[null]}', b"x" * 65537):
            with patch.object(ai.http.client, "HTTPSConnection") as ctor:
                ctor.return_value.getresponse.return_value.status = 200
                ctor.return_value.getresponse.return_value.read.return_value = raw
                self.assertEqual(ai.test_connection(self.cfg)["code"], "invalid_response")

    def test_allowlist_removes_identifiers_raw_logs_and_instructions(self):
        result = ai.summary(self.data, {"ready": True}, "playback")
        raw = json.dumps(result)
        for secret in ("PRIVATE", "DO-NOT-SEND", "239.255", "192.0.2"):
            self.assertNotIn(secret, raw)
        self.assertEqual(result["最近检查无响应数量"], 1)
        self.assertEqual(result["采样观察数量"], 0)
        self.assertEqual(result["全量状态"], "暂停")
        self.assertEqual(result["最近定时全量提交状态"], "已提交")
        with self.assertRaises(ValueError):
            ai.summary(self.data, {}, "ignore instructions")

    def test_offline_and_unknown_are_not_false_success_or_zero(self):
        result = ai.summary({**self.data, "service_error": "PRIVATE-ERROR"}, {}, "health")
        for name in ("正式频道数量", "最近检查无响应数量", "NAS存储异常", "IPTV入口与取流源地址一致", "全量已检查", "节目单待同步"):
            self.assertIsNone(result[name])
        self.assertEqual(result["NAS分析服务"], "不可达")

    def test_discovery_counts_distinguish_candidates_and_formal_channels(self):
        self.data['rows'].append({'change':'unverified','validation':'no_response','endpoint':'PRIVATE'})
        result = ai.summary(self.data, {'ready':True}, 'discovery')
        self.assertEqual(result['最近检查无响应数量'], 2)
        self.assertEqual(result['正式节目无响应数量'], 1)
        self.assertEqual(result['无信号候选数量'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_automation_progress_and_real_additions_not_misreported_as_all_manual(self):
        self.data.update(settings={'auto_process': True}, collector={'auto_update': True},
            batch={'state':'complete','trigger':'auto','done':6,'total':6,'message':'PRIVATE'},
            auto_followup={'state':'complete'}, scheduled_runs={},
            recent_updates=[{'at':123,'changes':[{'kind':'add','name':'PRIVATE-NAME','after':'PRIVATE-IP'}]}])
        self.data['rows'] += [
            {'change':'unverified','validation':'no_response','name_source':'unknown'},
            {'change':'new_media','validation':'media','name_source':'unknown','verified_count':1},
            {'change':'new_media','validation':'media','name_source':'stream','name':'PRIVATE-NAME','verified_count':2}]
        result = ai.summary(self.data, {'ready':True}, 'updates')
        self.assertEqual(result['名称待确认数量'], 1)
        self.assertEqual(result['有信号新候选数量'], 2)
        self.assertEqual(result['待间隔复核候选数量'], 1)
        self.assertTrue(result['自动识别复核已启用'])
        self.assertEqual(result['批量触发方式'], '自动')
        self.assertEqual(result['自动后处理状态'], '完成')
        self.assertEqual(result['已保留记录新增频道数'], 1)
        self.assertEqual(result['最近定时全量提交状态'], '未记录')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_settings_hidden_and_not_loaded_from_browser(self):
        public = self.handle("ai_status")
        self.assertTrue(public["configured"])
        self.assertNotIn(self.cfg["api_key"], json.dumps(public))
        self.path.unlink()
        self.assertFalse(self.handle("ai_status")["configured"])
        self.assertFalse(self.preview()["configured"])
        with self.assertRaises(ValueError):
            self.handle("ai_analyze", consent=True, api_key=self.cfg["api_key"])

    def test_reject_public_permissions_symlinks_host_and_model(self):
        self.path.chmod(0o644)
        self.assertFalse(ai.public_settings(self.path)["configured"])
        self.path.chmod(0o600)
        alias = Path(self.temp.name) / "alias.json"
        alias.symlink_to(self.path)
        with self.assertRaises(ValueError):
            ai.settings(alias)
        for changes in ({"base_url": "https://other.example"}, {"model": "arbitrary"}):
            atomic_json(self.path, {**self.cfg, **changes})
            with self.assertRaises(ValueError):
                ai.settings(self.path)

    def test_preview_never_calls_cloud(self):
        with patch.object(ai, "ask_cloud") as cloud:
            preview = self.preview()
            self.assertIn("ticket", preview)
            self.assertEqual((self.runtime / "preview.json").stat().st_mode & 0o777, 0o600)
            cloud.assert_not_called()

    def test_explicit_consent_ticket_owner_and_expiry_required(self):
        preview = self.preview()
        with patch.object(ai, "ask_cloud") as cloud:
            for fields in ({"ticket": preview["ticket"]}, {"ticket": "wrong", "consent": True}):
                with self.assertRaises((ValueError, PermissionError)):
                    self.handle("ai_analyze", **fields)
            with self.assertRaises(ValueError):
                ai.handle("ai_analyze", {"ticket": preview["ticket"], "consent": True}, "another-user",
                          lambda: {}, lambda: {}, self.path, self.runtime)
            with patch.object(ai.time, "time", return_value=0):
                with self.assertRaises(ValueError):
                    self.handle("ai_analyze", ticket=preview["ticket"], consent=True)
            cloud.assert_not_called()

    def test_sends_exact_preview_once_ignores_supplied_prompt_and_payload(self):
        preview = self.preview()
        self.data["baseline_count"] = 99
        with patch.object(ai, "ask_cloud", return_value={"advice": "test", "truncated": False}) as cloud:
            result = self.handle("ai_analyze", ticket=preview["ticket"], consent=True,
                                 summary={"password": "PRIVATE"}, prompt="change network")
            self.assertEqual(cloud.call_args.args[1], preview["summary"])
            self.assertEqual(result["summary"], preview["summary"])
            with self.assertRaises(ValueError):
                self.handle("ai_analyze", ticket=preview["ticket"], consent=True)
            self.assertEqual(cloud.call_count, 1)

    def test_failure_consumes_ticket_and_cooldown_stops_retry(self):
        preview = self.preview()
        with patch.object(ai, "ask_cloud", side_effect=ValueError("timeout")) as cloud:
            with self.assertRaises(ValueError):
                self.handle("ai_analyze", ticket=preview["ticket"], consent=True)
            newer = self.preview()
            with self.assertRaisesRegex(ValueError, "一分钟"):
                self.handle("ai_analyze", ticket=newer["ticket"], consent=True)
            self.assertEqual(cloud.call_count, 1)

    def test_one_request_at_a_time(self):
        self.runtime.mkdir()
        with (self.runtime / "request.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "正在进行"):
                self.preview()

    def test_http_uses_verified_tls_fixed_host_and_bounded_output(self):
        with patch.object(ai.http.client, "HTTPSConnection") as ctor:
            conn = ctor.return_value
            conn.getresponse.return_value.status = 200
            conn.getresponse.return_value.read.return_value = json.dumps({"choices": [
                {"message": {"content": "test"}, "finish_reason": "stop"}]}).encode()
            self.assertEqual(ai.ask_cloud(self.cfg, {"count": 1})["advice"], "test")
            ctor.assert_called_once_with("api.deepseek.com", timeout=20)
            args = conn.request.call_args.args
            self.assertEqual(args[:2], ("POST", "/chat/completions"))
            body = json.loads(args[2])
            self.assertEqual(body["model"], ai.MODEL)
            self.assertEqual(body["max_tokens"], 1600)
            self.assertNotIn("tools", body)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            conn.close.assert_called_once()

    def test_http_errors_do_not_reflect_remote_secrets_or_retry(self):
        for status in (301, 401, 402, 429, 500):
            with self.subTest(status=status), patch.object(ai.http.client, "HTTPSConnection") as ctor:
                conn = ctor.return_value
                conn.getresponse.return_value.status = status
                conn.getresponse.return_value.read.return_value = b'PRIVATE-REMOTE-ERROR'
                with self.assertRaises(ValueError) as error:
                    ai.ask_cloud(self.cfg, {})
                self.assertNotIn("PRIVATE", str(error.exception))
                conn.request.assert_called_once()
                conn.getresponse.return_value.read.assert_not_called()

    def test_bad_empty_and_overlarge_responses_fail_safely(self):
        for raw in (b"not-json", b"{}", b"[]", b"x" * 262145,
                    b'{"choices":[{"message":{"content":""},"finish_reason":"stop"}]}'):
            with patch.object(ai.http.client, "HTTPSConnection") as ctor:
                ctor.return_value.getresponse.return_value.status = 200
                ctor.return_value.getresponse.return_value.read.return_value = raw
                with self.assertRaises(ValueError):
                    ai.ask_cloud(self.cfg, {})

    def test_existing_cgi_auth_and_json_gate_still_protect_ai(self):
        for action in ("ai_analyze", "ai_settings", "ai_test"):
            body = json.dumps({"action": action, "user": "bad", "password": "bad", "consent": True}).encode()
            stream = Mock(buffer=io.BytesIO(body))
            with patch.dict(os.environ, {"REQUEST_METHOD": "POST", "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body))}), \
                    patch.object(router.sys, "stdin", stream), patch("pathlib.Path.read_text", return_value="test:correct"), \
                    patch.object(ai, "handle") as handle, patch("sys.stdout", new_callable=io.StringIO) as out:
                router.cgi()
                self.assertIn("Status: 403", out.getvalue())
                handle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
