"""Loopback-only UI test fixture. No router/NAS calls or production state writes."""
import argparse
import json
import mimetypes
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from server import Worker
from router import next_runs
from common import media_fingerprint

ASSETS = Path(__file__).resolve().parents[1] / "openwrt/data/www/iptv"
PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>自动发现 · 本地测试</title><link rel="stylesheet" href="/console.css">
<link rel="stylesheet" href="/discovery.css"><style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:24px}
input,select{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px;width:100%}
input[type=checkbox]{width:16px;height:16px}button{font:inherit;cursor:pointer}header{display:flex;gap:16px;align-items:center;margin-bottom:24px}
header h1{font-size:20px}header label{margin-left:auto}header input{width:auto}header small{color:var(--warn)}[hidden]{display:none!important}
</style><body class="iptv-modern"><main><header><h1>自动发现</h1><small>仅本地样例，不连接路由器或 NAS</small>
<label><input id="dark" type="checkbox">深色</label></header><section class="pane on" data-tab="capture"></section></main>
<script>sessionStorage.iptvAuthed='1';document.querySelector('#dark').addEventListener('change',e=>document.body.classList.toggle('iptv-dark',e.target.checked));</script>
<script src="/icons.js"></script><script src="/console.js"></script><script src="/discovery-model.js"></script><script src="/discovery.js"></script></body></html>"""


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, kind="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self.send(PAGE.encode(), "text/html")
        elif self.path[1:] in {"console.css", "discovery.css", "icons.js", "console.js", "discovery-model.js", "discovery.js"}:
            path = ASSETS / self.path[1:]
            self.send(path.read_bytes(), mimetypes.guess_type(path.name)[0])
        else:
            self.send(b"{}", code=404)

    def do_POST(self):
        try:
            if self.path != "/cgi-bin/iptv-discovery":
                raise ValueError("Local fixture only")
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 16384:
                raise ValueError("Invalid request")
            data = json.loads(self.rfile.read(size))
            worker = self.server.worker
            if data.get("action") in ("ai_status", "ai_preview", "ai_analyze", "ai_settings", "ai_test") and hasattr(self.server, "ai_runtime"):
                import ai_diagnostics
                from unittest.mock import patch
                kwargs = ({"side_effect": ValueError("本地样例：AI 请求超时，未自动重试")} if self.server.ai_fail else
                          {"return_value": {"advice": "本地模拟回复，未联系云端。\n已确认：入口就绪，存在近期无响应记录。\n证据不足：当前快照不能证明卡顿根因。\n建议：先核对实际播放现象，不自动重启或删除频道。", "truncated": False}})
                connection_result = ({"ok": False, "code": "timeout", "message": "本地模拟：连接测试超时"} if self.server.ai_fail else
                                     {"ok": True, "code": "connected", "message": "本地模拟：认证和模型列表正常", "latency_ms": 36})
                with patch("ai_diagnostics.ask_cloud", **kwargs), patch("ai_diagnostics.test_connection", return_value=connection_result):
                    result = ai_diagnostics.handle(data["action"], data, "fixture", lambda: (
                        {"service_error": "offline"} if self.server.offline else worker.status()),
                        lambda: {"ready": True}, self.server.ai_config_path, self.server.ai_runtime,
                        secure_transport=getattr(self.server, "ai_secure_fixture", False))
                if data["action"] == "ai_analyze":
                    self.server.writes.append("ai_analyze_simulated")
            elif data.get("action") == "status":
                offline = self.server.offline
                if self.server.outage_cycle:
                    offline = self.server.status_reads == 1
                    self.server.status_reads += 1
                result = ({"service_error": "NAS扫描服务暂不可用；已有结果可能过期，仅本地计划可读取"}
                          if offline else worker.status())
                result["collector"] = {"enabled": True, "daily_time": "09:30", "weekdays_only": True,
                                       "weekly_enabled": True, "weekly_day": 4, "weekly_time": "09:30", "auto_update": False}
                result["collector"].update(getattr(self.server, "collector_override", {}))
                result["recent_updates"] = getattr(self.server, "recent_updates", [])
                result["archive"] = getattr(self.server, "archive_status", {})
                result["next_runs"] = next_runs(result["collector"], {}, time.time())
                result["scheduled_runs"] = {
                    "sample": {"at": time.time()-3600, "state": "error", "message": "NAS通信失败，本次计划未完成；恢复后可手动执行"},
                    "audit": {"at": time.time()-86400, "state": "submitted", "message": "全量任务已提交，完成情况见检查进度"}}
                if self.server.sync_cycle:
                    result["collector"]["auto_update"] = True
                    result["update"] = {"state": "applied", "sync_pending": self.server.sync_reads == 0,
                                        "last_applied": {"at": time.time()-600, "changes": [{"kind": "add"}]}}
                    self.server.sync_reads += 1
            elif data.get("action") in ("verify", "audit", "batch"):
                from unittest.mock import patch
                # Exercise target selection, without starting network work.
                with patch("server.threading.Thread") as thread:
                    worker.start(data["action"], data)
                    targets = thread.call_args.kwargs["args"][1]
                worker.job_lock.release()
                if data["action"] in ("audit", "batch"):
                    worker.state[data["action"]].update(state="paused", message="本地模拟暂停，未真实取流")
                    worker.save()
                worker.job = {"state": "idle"}
                if hasattr(self.server,"writes"):
                    self.server.writes.append({"action":data["action"],"mode":data.get("mode"),"targets":len(targets)})
                result = {"message": f"本地样例：已选 {len(targets)} 项，未取流或改写节目单"}
            elif data.get("action") == "name":
                result = worker.confirm_name(data)
            elif data.get("action") == "settings" and hasattr(self.server, "collector_override"):
                if self.server.offline:
                    raise ValueError("本地样例：NAS 离线")
                if (data["settings"].get("auto_archive") and not self.server.collector_override.get("auto_archive")
                        and data.get("archive_consent") is not True):
                    raise ValueError("启用长期失效归档需单独确认")
                worker.settings(data["settings"])
                self.server.collector_override.update(data["settings"])
                self.server.writes.append("discovery_settings")
                result = {"message": "采样计划已保存（仅本地样例）"}
            else:
                raise ValueError("本地样例不执行采样、扫描、计划或节目单写入")
            self.send(json.dumps(result, ensure_ascii=False).encode())
        except (ValueError, TypeError, KeyError, PermissionError) as exc:
            self.send(json.dumps({"error": str(exc)}, ensure_ascii=False).encode(), code=400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage-error', action='store_true', help='Show a synthetic storage warning')
    parser.add_argument('--offline', action='store_true', help='Show a synthetic NAS outage')
    parser.add_argument('--outage-cycle', action='store_true', help='Second status request is offline, subsequent requests recover')
    parser.add_argument('--sync-cycle', action='store_true', help='Show pending playlist synchronization, then a successful acknowledgement')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="iptv-ui-fixture-") as temp:
        worker = Worker(Path(temp) / "state.json", "192.0.2.1")
        worker.state["baseline"] = [{"endpoint": "239.255.42.120:8000", "protocol": "rtp", "name": "示例频道"}]
        for suffix in (250, 251):
            worker.state["observations"][f"239.255.42.{suffix}:8000"] = {
                "validation": "media", "protocol": "rtp", "programs": [],
                "streams": [{"type": "video", "codec": "hevc", "width": 3840, "height": 2160}],
                "successes": 2, "first_success": time.time()-120, "last_success": time.time(), "last_seen": time.time()}
            item=worker.state["observations"][f"239.255.42.{suffix}:8000"]
            item.update(success_checks=[item['first_success'],item['last_success']],
                        success_fingerprint=media_fingerprint(f"239.255.42.{suffix}:8000",item))
        worker.state["observations"]["239.255.42.120:8000"] = {
            "validation": "media", "last_check": "no_response", "checked_at": time.time(),
            "last_seen": time.time(), "protocol": "rtp"}
        worker.state["catalog"] = {"at": time.time()-86400*90, "rows": [
            {"endpoint": "239.255.42.250:8000", "protocol": "rtp", "name": "测试频道甲"}]}
        worker.save()
        if args.storage_error:
            worker.storage_error = "状态保存失败，请检查存储空间和写入权限；自动更新已暂停"
        server = HTTPServer(("127.0.0.1", 0), Fixture)
        server.worker = worker
        server.offline = args.offline
        server.outage_cycle = args.outage_cycle
        server.status_reads = 0
        server.sync_cycle = args.sync_cycle
        server.sync_reads = 0
        print(f"Local fixture: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
