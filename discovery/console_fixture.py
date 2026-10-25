"""Loopback-only complete console preview. All writes stay in memory."""
import argparse
import json
import mimetypes
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ui_fixture import Fixture, ASSETS
from server import Worker
from common import media_fingerprint, playlist as parse_playlist


def playlist():
    names = ["示例卫视 HD", "示例新闻", "示例电影", "示例体育", "示例纪实", "示例少儿"]
    groups = ["卫视", "新闻", "影视", "体育", "纪实", "少儿"]
    lines = ["#EXTM3U"]
    for index in range(36):
        name = names[index % 6] + (f" {index//6+1}" if index >= 6 else "")
        lines.extend([f'#EXTINF:-1 tvg-id="demo{index}" group-title="{groups[index%6]}",{name}',
                      f"http://192.0.2.1:4022/rtp/239.255.42.{index+1}:8000"])
    return "\n".join(lines)+"\n"


class ConsoleFixture(Fixture):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/iptv/", "/iptv/index.html"):
            page = (ASSETS / "index.html").read_text()
            page = page.replace("<body class=", '<script>sessionStorage.iptvAuthed="1";</script><body class=', 1)
            page = page.replace('<footer class="console-footer">', '<footer class="console-footer"><b>本地样例 · 不连接生产设备</b>')
            self.send(page.encode(), "text/html")
        elif path == "/cgi-bin/iptv-status":
            self.send(json.dumps({"lan_ip": "192.0.2.1", "iptv_iface": "eth1", "iptv_link": "UP",
                                  "capture_iface": "eth2", "capture_link": "DOWN", "iptv_ip": "198.51.100.10",
                                  "proxy_status": "running", "nikki_state": "direct", "new_count": 36,
                                  "epg_status": "ok", "play_active_clients": 1, "play_total_seconds": 68400,
                                  "play_max_clients": 2, "play_total_mb": 12540, "iptv_fw_packets": 920,
                                  "multicast_route": "239.0.0.0/8 dev br-iptv",
                                  "play_top_channels": "示例卫视 HD=3600|示例电影=2100"}).encode())
        elif path == "/fixture/state":
            self.send(json.dumps({"writes": self.server.writes, "playlist": self.server.playlists}, ensure_ascii=False).encode())
        elif path.startswith("/iptv/"):
            target = (ASSETS / path.removeprefix("/iptv/")).resolve()
            if not target.is_relative_to(ASSETS.resolve()) or not target.is_file():
                self.send(b"{}", code=404)
            else:
                self.send(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "text/plain")
        else:
            self.send(b"{}", code=404)

    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/cgi-bin/iptv-discovery":
            return super().do_POST()
        if parsed.path != "/cgi-bin/iptv-action":
            return self.send(b"{}", code=404)
        query = parse_qs(parsed.query)
        action = query.get("action", [""])[0]
        file = query.get("file", ["new.m3u"])[0]
        body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 5000000)).decode()
        result = ""
        if action == "get_playlist":
            result = self.server.playlists.get(file, "")
        elif action == "save_playlist":
            if self.server.fail_save:
                return self.send("样例保存失败：只读存储".encode(), "text/plain", 503)
            self.server.history[file] = self.server.playlists[file]
            self.server.playlists[file] = body
            self.server.writes.append(action)
            result = "节目单已保存（仅本地样例）"
        elif action == "get_history":
            result = "示例历史版本|demo-backup|1024"
        elif action == "restore_history":
            self.server.playlists[file] = self.server.history.get(file, playlist())
            self.server.writes.append(action)
            result = "已恢复历史版本（仅本地样例）"
        elif action == "get_config":
            result = "title: IPTV 家庭网关\nlinks:\n- title: 标准节目单 | url: http://192.0.2.1:8080/iptv/new.m3u\n- title: 带台标节目单 | url: http://192.0.2.1:8080/iptv/new-logo.m3u\n- title: 电子节目单 | url: http://192.0.2.1:8080/iptv/epg.xml"
        elif action == "get_epg_config":
            result = "[epg_sources]\nhttps://example.com/epg.xml\n[logo_sources]\n\n[channel_aliases]\n示例卫视 HD=示例卫视\n[schedule]\nenabled=1\nkeep_days=14\nstart=09:00\nend=18:00\ninterval_hours=6\ninterval_minutes=0\n[status]\nEPG 资料已读取"
        elif action == "list_logos":
            result = "\n".join(f"{r}\t" for r in ["示例卫视 HD", "示例新闻", "示例电影", "示例体育", "示例纪实", "示例少儿"])
        elif action in ("get_ui_log", "get_capture_ui_log"):
            result = "本地样例：没有执行生产操作。"
        elif action == "auth":
            result = "OK"
        elif action in ("save_epg_config", "save_config", "save_ui_log", "save_capture_ui_log", "self_check", "analyze_playback"):
            self.server.writes.append(action)
            result = "本地样例：操作已记录，未连接设备。"
        else:
            return self.send("本地预览禁止执行此操作".encode(), "text/plain", 400)
        self.send(result.encode(), "text/plain")


def make_server(temp, port=0, offline=False, fail_save=False):
    worker = Worker(Path(temp)/"state.json", "192.0.2.1")
    worker.state["baseline"] = parse_playlist(playlist())
    for suffix in (250, 251):
        endpoint = f"239.255.42.{suffix}:8000"
        item = {"validation": "media", "protocol": "rtp", "programs": [],
                "streams": [{"type": "video", "codec": "hevc", "width": 3840, "height": 2160}],
                "successes": 2, "first_success": time.time()-120, "last_success": time.time(),
                "last_seen": time.time(), "success_checks": [time.time()-120, time.time()]}
        item["success_fingerprint"] = media_fingerprint(endpoint, item)
        worker.state["observations"][endpoint] = item
    worker.state["observations"]["239.255.42.1:8000"] = {"validation": "media", "last_check": "no_response", "checked_at": time.time(), "protocol": "rtp"}
    worker.state["history"] = [{"kind": "sample", "at": time.time()-900, "state": "complete", "observed": 3},
                               {"kind": "verify", "at": time.time()-1800, "state": "complete"},
                               {"kind": "audit", "at": time.time()-3600, "state": "paused", "message": "检测到播放，已保留进度"}]
    worker.save()
    server = ThreadingHTTPServer(("127.0.0.1", port), ConsoleFixture)
    server.worker, server.offline = worker, offline
    server.outage_cycle = server.sync_cycle = False
    server.status_reads = server.sync_reads = 0
    server.fail_save = fail_save
    server.playlists = {key: playlist() for key in ("new.m3u", "new-logo.m3u", "vsp.m3u")}
    server.history, server.writes = {}, []
    server.collector_override = {}
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--fail-save", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="iptv-console-") as temp:
        server = make_server(temp, args.port, args.offline, args.fail_save)
        print(f"Local console preview: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
