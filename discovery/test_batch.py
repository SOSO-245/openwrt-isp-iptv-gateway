"""Bounded batch workflows using synthetic media and an advancing clock."""
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import Worker
from playlist_update import apply as apply_playlist
from common import playlist


class BatchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.worker = Worker(Path(temp.name) / "state.json", "192.0.2.1")
        self.base = "239.255.42.1:8000"
        self.key = "239.255.42.2:8000"
        self.worker.ingest({"baseline": [{"endpoint": self.base, "protocol": "udp", "name": "Example A"}]})
        self.worker.observe(self.key, {"validation": "no_response"})
        self.worker.save()
        self.worker.set_link({"ready": True, "generation": "synthetic"})
        self.media = {"validation": "media", "protocol": "udp", "programs": [],
                      "streams": [{"type": "video", "codec": "h264"}]}

    def prepare(self, **data):
        with patch("server.threading.Thread") as thread:
            self.worker.start("batch", {"endpoints": [self.key], **data})
        return thread.call_args.kwargs["args"][1]

    def run_batch(self, targets, probe):
        now = [time.time()]
        def wait(seconds):
            now[0] += seconds
            return False
        with patch("server.time.time", side_effect=lambda: now[0]), \
                patch("common.time.time", side_effect=lambda: now[0]), \
                patch.object(self.worker.cancel, "wait", side_effect=wait), \
                patch.object(self.worker, "link_ready", return_value=True), \
                patch.object(self.worker, "busy", return_value=False), \
                patch.object(self.worker, "probe", side_effect=probe) as mock:
            self.worker.run("batch", targets)
            rows = self.worker.rows()
        return mock, rows

    def test_smart_identifies_and_rechecks_same_stream_after_interval(self):
        targets = self.prepare(workflow="smart")
        named = {**self.media, "programs": [{"id": "1", "name": "Example B"}], "check_method": "deep"}
        calls, rows = self.run_batch(targets, [self.media, named, named])
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(calls.call_args_list[-1].kwargs, {"deep": True})
        row = next(r for r in rows if r["endpoint"] == self.key)
        self.assertEqual(row["name_source"], "stream")
        self.assertEqual(row["verified_count"], 2)
        self.assertGreaterEqual(row["success_checks"][1] - row["success_checks"][0], 60)
        self.assertEqual(self.worker.state["batch"]["done"], 3)
        self.assertFalse(self.worker.job_lock.locked())

    def test_no_response_skips_identification_and_recheck(self):
        targets = self.prepare()
        calls, rows = self.run_batch(targets, [{"validation": "no_response"}])
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.worker.state["batch"]["counts"], {"no_response": 1, "skipped": 2})
        self.assertEqual(len(self.worker.state["baseline"]), 1)
        self.assertEqual(rows[0]["verified_count"], 0)

    def test_unknown_name_remains_unknown_even_with_two_successes(self):
        targets = self.prepare()
        _, rows = self.run_batch(targets, [self.media, {**self.media, "check_method": "deep"}, {**self.media, "check_method": "deep"}])
        self.assertEqual(rows[0]["name_source"], "unknown")
        self.assertEqual(rows[0]["verified_count"], 2)
        self.assertEqual(self.worker.state["baseline"][0]["name"], "Example A")

    def test_limits_duplicates_and_unknown_endpoints_rejected(self):
        for data in ({"endpoints": [self.key] * 33}, {"endpoints": [self.key] * 2},
                     {"workflow": "identify", "endpoints": [self.key] * 9},
                     {"endpoints": ["239.255.42.99:8000"]}, {"workflow": "delete"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.prepare(**data)
            self.assertFalse(self.worker.job_lock.locked())

    def test_playback_pauses_and_restart_preserves_resume_position(self):
        targets = self.prepare(workflow="verify")
        with patch.object(self.worker, "busy", return_value=True), patch.object(self.worker, "probe") as probe:
            self.worker.run("batch", targets)
        probe.assert_not_called()
        self.assertEqual(self.worker.state["batch"]["done"], 0)
        self.worker = Worker(self.worker.path, self.worker.router)
        resumed = self.prepare(mode="resume")
        self.assertEqual(resumed, targets)
        self.worker.job_lock.release()

    def test_pause_during_recheck_wait_does_not_forge_success(self):
        targets = self.prepare()
        named = {**self.media, "programs": [{"id": "1", "name": "Example B"}]}
        def wait(seconds):
            if seconds > 5:
                self.worker.cancel.set()
        with patch.object(self.worker.cancel, "wait", side_effect=wait), \
                patch.object(self.worker, "busy", return_value=False), \
                patch.object(self.worker, "probe", return_value=named) as probe:
            self.worker.run("batch", targets)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(self.worker.state["batch"]["state"], "paused")
        self.assertEqual(self.worker.rows()[0]["verified_count"], 1)

    def test_auto_batch_cannot_replace_unfinished_manual_batch(self):
        self.worker.state["settings"]["auto_process"] = True
        self.worker.state["batch"] = {"state": "paused", "done": 1, "total": 3}
        before = copy.deepcopy(self.worker.state["batch"])
        with self.assertRaisesRegex(ValueError, "尚未完成"):
            self.prepare(trigger="auto")
        self.assertEqual(self.worker.state["batch"], before)

    def test_auto_batch_requires_setting(self):
        with self.assertRaisesRegex(ValueError, "已关闭"):
            self.prepare(trigger="auto")

    def test_audit_auto_followup_is_bounded_and_failure_visible(self):
        self.worker.state["settings"]["auto_process"] = True
        self.worker.observe(self.key, self.media)
        with patch("server.threading.Thread"):
            self.worker.start("audit", {"mode": "new"})
        self.worker.job["done"] = self.worker.job["total"]
        with patch.object(self.worker, "start", side_effect=ValueError("另一个任务运行中")) as start:
            self.worker.run("audit", self.worker.state["audit"]["targets"])
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.args[0], "batch")
        self.assertEqual(self.worker.status()["auto_followup"]["state"], "blocked")
        self.assertNotIn("targets", self.worker.status()["audit"])

    def test_auto_audit_followup_deep_reads_short_named_candidate_and_adds_playlist(self):
        self.worker.state["settings"]["auto_process"] = True
        short = {**self.media, "programs": [{"id": "1", "name": "Example B"}],
                 "streams": [{"type": "video", "codec": "h264", "width": 0, "height": 0}],
                 "check_method": "short"}
        deep = {**short, "streams": [{"type": "video", "codec": "h264", "width": 1920, "height": 1080}],
                "check_method": "deep"}
        self.worker.observe(self.key, short)
        with patch("server.threading.Thread"):
            self.worker.start("audit", {"mode": "new", "trigger": "scheduled"})
        self.worker.job["done"] = self.worker.job["total"]
        with patch("server.threading.Thread") as thread:
            self.worker.run("audit", self.worker.state["audit"]["targets"])
        kind, targets = thread.call_args.kwargs["args"]
        self.assertEqual(kind, "batch")
        self.assertEqual(self.worker.state["batch"]["trigger"], "auto")
        calls, rows = self.run_batch(targets, [short, deep, deep])
        self.assertEqual(calls.call_count, 3)
        self.assertEqual([c.kwargs for c in calls.call_args_list], [{}, {"deep": True}, {"deep": True}])
        self.assertEqual(self.worker.status()["auto_followup"]["state"], "complete")
        self.assertEqual(self.worker.state["history"][0]["trigger"], "auto")
        www = self.worker.path.parent / "www"
        www.mkdir()
        original = f"#EXTM3U\n#EXTINF:-1,Example A\nhttp://192.0.2.1:12123/udp/{self.base}\n"
        for name in ("new.m3u", "new-logo.m3u"):
            (www / name).write_text(original)
        (www / "new.txt").write_text(f"Example A,http://192.0.2.1:12123/udp/{self.base}\n")
        now = max(r.get("last_success", 0) for r in rows) + 1
        with patch("playlist_update.time.time", return_value=now):
            result = apply_playlist(www, self.worker.path.parent / "backups", rows)
            self.assertEqual(result["state"], "applied")
            self.assertEqual(result["changes"][0]["kind"], "add")
            self.assertEqual(len(playlist((www / "new.m3u").read_text())), 2)
            self.assertEqual((www / "new.m3u").read_text(), (www / "new-logo.m3u").read_text())
            self.assertIn("Example B,", (www / "new.txt").read_text())
            self.worker.ingest({"baseline": playlist((www / "new.m3u").read_text())})
            self.assertEqual(self.worker.status()["baseline_count"], 2)
            self.assertEqual(apply_playlist(www, self.worker.path.parent / "backups", self.worker.rows())["state"], "unchanged")

    def test_deep_recheck_without_name_does_not_reuse_short_name(self):
        targets = self.prepare()
        named = {**self.media, "programs": [{"id": "1", "name": "Example B"}], "check_method": "deep"}
        _, rows = self.run_batch(targets, [named, named, {**named, "programs": [{"id": "1", "name": ""}]}])
        from playlist_update import eligible
        self.assertIsNone(eligible(rows[0], rows[0]["last_success"] + 1))
        self.assertEqual(rows[0]["verified_count"], 1)


if __name__ == "__main__":
    unittest.main()
