"""LAN-only, bounded IPTV discovery worker. Never writes the live playlist."""
import collections
import copy
import hmac
import http.client
import ipaddress
import json
import os
import subprocess
import threading
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from common import (atomic_json, channel_name, differences, endpoint, media_fingerprint,
                    multicast_ranges, recent_checks, split_endpoint, successful_checks)


class StatusTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in ("td", "th"):
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        if tag == "tr":
            self.rows.append(self.row)


def active_clients(html):
    parser = StatusTable()
    parser.feed(html)
    for index, row in enumerate(parser.rows[:-1]):
        if "Active clients" in row:
            count = int(parser.rows[index + 1][row.index("Active clients")])
            if 0 <= count <= 10000:
                return count
    raise ValueError("无法确认 udpxy 播放状态，已停止主动取流")


def media_details(data, deep=False):
    # Parsing is delegated to FFmpeg, with no network/file access from the demuxer.
    proc = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "pipe",
                           "-f", "mpegts", "-analyzeduration", "8000000" if deep else "1500000",
                           "-probesize", "8388608" if deep else "1048576",
                           "-show_programs", "-show_streams", "-of", "json", "pipe:0"],
                          input=data, capture_output=True, timeout=5 if deep else 3)
    if proc.returncode != 0 or len(proc.stdout) > 262144:
        return {"validation": "no_media"}
    parsed = json.loads(proc.stdout)
    streams = [{"type": s.get("codec_type"), "codec": s.get("codec_name"),
                "width": s.get("width"), "height": s.get("height")}
               for s in parsed.get("streams", [])[:32]
               if s.get("codec_type") in ("audio", "video") and s.get("codec_name") not in (None, "unknown")]
    programs = [{"id": str(p.get("program_id", ""))[:32],
                 "name": str(p.get("tags", {}).get("service_name", ""))[:160]}
                for p in parsed.get("programs", [])[:32]]
    return {"validation": "media" if streams else "no_media", "programs": programs, "streams": streams}


class Worker:
    def __init__(self, path, router):
        ip = ipaddress.ip_address(router)
        if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_multicast:
            raise ValueError("Invalid router address")
        self.path, self.router = Path(path), str(ip)
        self.lock, self.job_lock = threading.RLock(), threading.Lock()
        self.cancel = threading.Event()
        self.link = {}
        self.state = {"version": 1, "baseline": [], "observations": {}, "history": [],
                      "settings": {"active_enabled": False, "ranges": [], "ports": []}, "cursor": 0}
        if self.path.exists():
            self.state.update(json.loads(self.path.read_text()))
        self.saved_state = copy.deepcopy(self.state)
        self.storage_error = ""
        self.job = {"state": "idle"}
        if self.state.get("audit", {}).get("state") == "running":
            self.state["audit"].update(state="paused", message="服务重启，已保留全量检查进度")
            self.save()

    def save(self):
        try:
            atomic_json(self.path, self.state)
        except OSError:
            # Failed confirmations/probes must not authorize updates from memory.
            self.state = copy.deepcopy(self.saved_state)
            self.storage_error = "状态保存失败，请检查存储空间和写入权限；自动更新已暂停"
            raise
        self.saved_state = copy.deepcopy(self.state)
        self.storage_error = ""

    def rows(self):
        return differences(self.state["baseline"], self.state["observations"], self.state.get("catalog"))

    def status(self):
        with self.lock:
            allowed = sorted({str(ipaddress.ip_network(split_endpoint(r["endpoint"])[0] + "/24", strict=False))
                              for r in self.state["baseline"]})
            ports = [p for p, _ in collections.Counter(split_endpoint(r["endpoint"])[1]
                                                     for r in self.state["baseline"]).most_common()]
            return copy.deepcopy({"rows": self.rows(), "history": self.state["history"],
                                  "settings": self.state["settings"], "job": self.job,
                                  "audit": {k: v for k, v in self.state.get("audit", {}).items() if k != "targets"},
                                  "link": self.link, "storage_error": self.storage_error,
                                  "catalog": {"count": len(self.state.get("catalog", {}).get("rows", [])),
                                              "at": self.state.get("catalog", {}).get("at", 0)},
                                  "baseline_count": len(self.state["baseline"]), "allowed_ranges": allowed,
                                  "allowed_ports": ports, "limits": {"scan": 8, "verify": 4, "identify": 1}})

    def record(self, kind, before, details):
        self.state["observations"] = dict(sorted(self.state["observations"].items(),
                                                 key=lambda x: x[1].get("last_seen", 0), reverse=True)[:1024])
        now_rows = {r["endpoint"]: r for r in self.rows()}
        def signature(row):
            return {k: row.get(k) for k in ("change", "name", "name_source", "validation", "previous", "streams")}
        delta = [{"endpoint": k, "before": signature(before[k]) if k in before else None,
                  "after": signature(v)} for k, v in now_rows.items()
                 if k not in before or signature(before[k]) != signature(v)]
        if "results" in details:
            details["result_count"] = len(details["results"])
            details["results"] = details["results"][:32]
        self.state["history"].insert(0, {"at": time.time(), "kind": kind, "changes": delta[:64], **details})
        self.state["history"] = self.state["history"][:30]
        self.save()

    def ingest(self, data):
        raw = data.get("baseline", [])
        if not isinstance(raw, list) or not 1 <= len(raw) <= 4096:
            raise ValueError("Invalid baseline")
        base = []
        for r in raw:
            group, port = split_endpoint(r["endpoint"])
            if r.get("protocol") not in ("udp", "rtp"):
                raise ValueError("Invalid protocol")
            base.append({"endpoint": endpoint(group, port), "protocol": r["protocol"],
                         "name": str(r.get("name", ""))[:160], "channel_id": str(r.get("channel_id", ""))[:160]})
        items = data.get("observations", [])
        if not isinstance(items, list) or len(items) > 600:
            raise ValueError("Invalid sample")
        cleaned = []
        for item in items:
            group, port = split_endpoint(item["endpoint"])
            packets = int(item["packets"])
            if not 1 <= packets <= 600:
                raise ValueError("Invalid packet count")
            cleaned.append((endpoint(group, port), packets))
        if sum(n for _, n in cleaned) > 600:
            raise ValueError("Sample exceeds packet limit")
        catalog = None
        if "catalog" in data:
            raw_catalog = data["catalog"]
            if not isinstance(raw_catalog, dict) or not isinstance(raw_catalog.get("rows"), list) or len(raw_catalog["rows"]) > 4096:
                raise ValueError("Invalid channel catalog")
            stamp = raw_catalog.get("at", 0)
            if type(stamp) not in (float, int) or not 0 <= stamp <= time.time() + 300:
                raise ValueError("Invalid catalog time")
            catalog = {"at": stamp, "rows": []}
            for row in raw_catalog["rows"]:
                group, port = split_endpoint(row["endpoint"])
                name = channel_name(row.get("name"))
                if name and row.get("protocol") in ("udp", "rtp"):
                    catalog["rows"].append({"endpoint": endpoint(group, port), "name": name, "protocol": row["protocol"]})
        with self.lock:
            before = {r["endpoint"]: r for r in self.rows()}
            self.state["baseline"] = base
            if catalog is not None:
                self.state["catalog"] = catalog
            for key, packets in cleaned:
                item = self.state["observations"].setdefault(key, {"validation": "pending", "first_seen": time.time()})
                item.update(last_seen=time.time(), packets=packets, source="passive")
            # Retain recent observations, not an unbounded packet log.
            self.state["observations"] = dict(sorted(self.state["observations"].items(),
                                                     key=lambda x: x[1].get("last_seen", 0), reverse=True)[:1024])
            if data.get("trigger") == "baseline":
                self.save()
                return {"message": "节目单基线已同步"}
            self.record("sample", before, {"observed": len(cleaned), "trigger": str(data.get("trigger", ""))[:16]})
            active = self.state["settings"]["active_enabled"] and data.get("trigger") == "scheduled"
        if active:
            try:
                self.start("scan", {})
            except ValueError as exc:
                with self.lock:
                    self.record("scan", {}, {"state": "skipped", "message": str(exc)})
        return {"message": "采样完成", "observed": len(cleaned)}

    def confirm_name(self, data):
        with self.lock:
            if self.job_lock.locked():
                raise ValueError("请等待检查结束后确认名称")
            key = data.get("endpoint")
            item = self.state["observations"].get(key)
            if item is None or any(r["endpoint"] == key for r in self.state["baseline"]):
                raise ValueError("仅可确认已发现的新地址；原节目请在节目单编辑")
            if data.get("fingerprint") != media_fingerprint(key, item):
                raise ValueError("识别结果已变化，请刷新后重新核对")
            before = {r["endpoint"]: r for r in self.rows()}
            if data.get("revoke") is True:
                item.pop("confirmation", None)
                message = "名称确认已撤回；已写入正式节目单的条目不会自动删除"
            else:
                name = channel_name(data.get("name"))
                if not name or data.get("reviewed") is not True:
                    raise ValueError("请填写有效频道名称并确认已核对")
                if (item.get("validation") != "media" or item.get("last_check")
                        or not 0 <= time.time() - item.get("last_success", 0) <= 7 * 86400
                        or len(item.get("programs", [])) > 1):
                    raise ValueError("请先验证音视频；多节目流不支持单名称确认")
                item["confirmation"] = {"name": name, "at": time.time(), "fingerprint": media_fingerprint(key, item)}
                message = "名称已确认；仍需通过重复验证才会自动入库"
            self.record("name", before, {"message": message})
            return {"message": message}

    def settings(self, data):
        if type(data.get("active_enabled")) is not bool:
            raise ValueError("Invalid active setting")
        with self.lock:
            if self.job_lock.locked():
                raise ValueError("请等待当前扫描结束后修改设置")
            ranges = data.get("ranges", [])
            if not isinstance(ranges, list) or len(ranges) > 16:
                raise ValueError("Invalid ranges")
            networks = multicast_ranges(ranges, self.state["baseline"])
            allowed = {split_endpoint(r["endpoint"])[1] for r in self.state["baseline"]}
            ports = data.get("ports", [])
            if not isinstance(ports, list) or len(ports) > 2 or any(type(p) is not int or p not in allowed for p in ports):
                raise ValueError("最多选择两个现有节目端口")
            if data["active_enabled"] and (not networks or not ports):
                raise ValueError("主动扫描需要先设置范围和端口")
            new = {"active_enabled": data["active_enabled"], "ranges": [str(n) for n in networks], "ports": ports}
            if new != self.state["settings"]:
                self.state.update(settings=new, cursor=0)
                self.save()
        return {"message": "设置已保存"}

    def busy(self):
        conn = http.client.HTTPConnection(self.router, 12123, timeout=2)
        try:
            conn.request("GET", "/status/")
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError("无法确认播放状态，已停止主动取流")
            body = response.read(131073)
            if len(body) > 131072:
                raise ValueError("播放状态响应过大")
            return active_clients(body.decode("utf-8", "replace")) > 0
        finally:
            conn.close()

    def set_link(self, data):
        if type(data.get("ready")) is not bool or not isinstance(data.get("generation"), str):
            raise ValueError("Invalid link state")
        with self.lock:
            self.link = {"ready": data["ready"], "generation": data["generation"][:80],
                         "message": str(data.get("message", ""))[:160], "at": time.time()}
        return {"message": "入口状态已同步"}

    def link_ready(self):
        return self.link.get("ready") and time.time() - self.link.get("at", 0) < 45

    def probe(self, key, deep=False):
        group, port = split_endpoint(key)
        with self.lock:
            protocol = next((r["protocol"] for r in self.state["baseline"] if r["endpoint"] == key),
                            self.state["observations"].get(key, {}).get("protocol", "udp"))
        conn = http.client.HTTPConnection(self.router, 12123, timeout=1)
        body, started = bytearray(), time.monotonic()
        try:
            conn.request("GET", f"/{protocol}/{group}:{port}")
            response = conn.getresponse()
            if response.status != 200:
                return {"validation": "no_media", "http_status": response.status}
            limit = 8388608 if deep else 1048576
            while len(body) < limit and time.monotonic() - started < (10 if deep else 4):
                if self.cancel.is_set():
                    return {"validation": "inconclusive", "bytes_checked": len(body)}
                chunk = response.read1(min(65536, limit - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
        except (OSError, http.client.HTTPException):
            if not body:
                return {"validation": "no_response"}
        finally:
            conn.close()
        try:
            return dict(media_details(bytes(body), deep=deep), bytes_checked=len(body), protocol=protocol,
                        check_method="deep" if deep else "short")
        except (subprocess.TimeoutExpired, ValueError):
            return {"validation": "inconclusive", "bytes_checked": len(body)}

    def start(self, kind, data):
        if not self.job_lock.acquire(blocking=False):
            raise ValueError("已有验证或扫描任务运行")
        try:
            with self.lock:
                self.cancel.clear()
                if kind == "scan":
                    opts = self.state["settings"]
                    networks = multicast_ranges(opts["ranges"], self.state["baseline"])
                    if not networks or not opts["ports"]:
                        raise ValueError("请先保存扫描网段和端口")
                    known = {r["endpoint"] for r in self.state["baseline"]}
                    pool = [endpoint(str(ip), port) for n in networks for ip in n for port in opts["ports"]
                            if endpoint(str(ip), port) not in known]
                    if not pool:
                        raise ValueError("范围内均为已知地址")
                    offset = self.state["cursor"] % len(pool)
                    # Recheck discoveries before rotating into new addresses.
                    pending = [k for k, v in self.state["observations"].items()
                               if k not in known and v.get("successes", 0) < 2
                               and time.time() - v.get("checked_at", 0) >= 60][:4]
                    targets = list(dict.fromkeys(pending + pool[offset:] + pool[:offset]))[:8]
                elif kind == "audit":
                    old = self.state.get("audit", {})
                    scheduled = data.get("trigger") == "scheduled"
                    if not scheduled and old.get("state") in ("paused", "error") and old.get("done", 0) < old.get("total", 0):
                        targets = old["targets"]
                    else:
                        opts = self.state["settings"]
                        networks = multicast_ranges(opts["ranges"], self.state["baseline"])
                        discovery = [endpoint(str(ip), port) for n in networks for ip in n for port in opts["ports"]]
                        targets = list(dict.fromkeys([r["endpoint"] for r in self.state["baseline"]]
                                                    + list(self.state["observations"]) + discovery))
                        if not targets or len(targets) > 4096:
                            raise ValueError("正式节目单为空或超出检查上限")
                        self.state["audit"] = {"targets": targets, "done": 0, "total": len(targets),
                                               "started": time.time(), "counts": {},
                                               "trigger": "scheduled" if scheduled else "manual"}
                        if old:
                            self.state["audit"]["previous"] = {
                                k: copy.deepcopy(old[k]) for k in
                                ("state", "done", "total", "started", "finished", "counts") if k in old}
                    self.state["audit"].update(state="running", message="")
                    self.save()
                else:
                    targets = data.get("endpoints", [])
                    if kind == "identify" and (not isinstance(targets, list) or len(targets) != 1):
                        raise ValueError("深度识别每次仅限一个已记录地址")
                    if not isinstance(targets, list) or len(targets) > 4:
                        raise ValueError("每次最多验证四个地址")
                    allowed = {r["endpoint"] for r in self.state["baseline"]} | self.state["observations"].keys()
                    if targets and any(k not in allowed for k in targets):
                        raise ValueError("只能验证已记录的地址")
                    if not targets:
                        rows = sorted(self.rows(), key=lambda r: (r.get("checked_at", 0), r["endpoint"]))
                        if data.get("retry_failed") is True:
                            targets = [r["endpoint"] for r in rows
                                       if r.get("last_check", r.get("validation")) in ("no_response", "no_media", "inconclusive")][:4]
                        else:
                            targets = [r["endpoint"] for r in rows
                                       if r.get("validation") != "media" or r.get("last_check")
                                       or (r["change"] != "existing" and len(successful_checks(r["endpoint"], r, time.time())) < 2)][:4]
                    if not targets:
                        raise ValueError("暂无待验证地址")
                self.job = {"state": "running", "kind": kind, "total": len(targets), "done": 0, "started": time.time()}
                if kind == "audit":
                    self.job["done"] = self.state["audit"]["done"]
            threading.Thread(target=self.run, args=(kind, targets), daemon=True).start()
        except Exception:
            try:
                with self.lock:
                    if self.job.get("state") == "running":
                        self.job.update(state="error", message="任务未能启动，可以稍后重试")
                        if kind == "audit" and self.state.get("audit", {}).get("state") == "running":
                            self.state["audit"].update(state="paused", message=self.job["message"])
                            try:
                                self.save()
                            except OSError:
                                pass
            finally:
                self.job_lock.release()
            raise
        return {"message": "任务已提交", "count": len(targets)}

    def pause(self):
        self.cancel.set()
        return {"message": "将在当前频道检查结束后暂停"}

    def drain(self):
        # udpxy's default receive timeout is 5s. Give our closed probe time to
        # disappear before treating an active connection as a real viewer.
        for _ in range(10):
            if not self.busy():
                return True
            if self.cancel.wait(1):
                return False
        return False

    def observe(self, key, result):
        now = time.time()
        item = self.state["observations"].setdefault(key, {"first_seen": now, "source": "active"})
        if result["validation"] == "media":
            fingerprint = media_fingerprint(key, result)
            checks = recent_checks(item.get("success_checks", []), now) if item.get("success_fingerprint") == fingerprint else []
            if not checks or now - checks[-1] >= 60:
                checks.append(now)
            checks = checks[-2:]
            item.update(success_fingerprint=fingerprint, success_checks=checks,
                        successes=len(checks), first_success=checks[0], daytime_failure_checks=[])
            item.update(result, last_success=now, failures=0)
            confirmation = item.get("confirmation")
            if confirmation and confirmation.get("fingerprint") != media_fingerprint(key, item):
                confirmation["invalidated"] = True
            item.setdefault("first_success", now)
            item.pop("last_check", None)
            item.pop("first_failure", None)
        else:
            # A failed/inconclusive check breaks the success streak, not the last
            # known media details shown to the user. Old undated counts are untrusted.
            item.update(successes=0, success_checks=[])
            item.pop("success_fingerprint", None)
            checks = recent_checks(item.get("daytime_failure_checks", []), now, spacing=3600, daytime=True)
            if result["validation"] == "no_response":
                checks = recent_checks(checks + [now], now, spacing=3600, daytime=True)
            item["daytime_failure_checks"] = checks[-64:]
            if now - item.get("last_failure", 0) >= 3600:
                item["failures"] = item.get("failures", 0) + 1
            item.setdefault("first_failure", now)
            item["last_failure"] = now
            if item.get("validation") == "media":
                item["last_check"] = result["validation"]
            else:
                item.update(result)
        item.update(checked_at=now, last_seen=now)

    def run(self, kind, targets):
        before, results = {}, []
        try:
            with self.lock:
                before = {r["endpoint"]: r for r in self.rows()}
            for key in targets[self.job.get("done", 0):]:
                if self.cancel.is_set() or time.time() - self.job["started"] >= 3600:
                    self.job.update(state="paused", message="检查已暂停，进度已保留")
                    break
                if not self.link_ready():
                    self.job.update(state="paused", message="IPTV 入口状态异常或过期，未判定频道失效")
                    break
                if self.busy():
                    self.job.update(state="paused", message="检测到活动取流，已暂停；可空闲时继续")
                    break
                generation = self.link.get("generation")
                result = self.probe(key, deep=True) if kind == "identify" else self.probe(key)
                if self.cancel.is_set():
                    self.job.update(state="paused", message="检查已取消，本次结果未写入")
                    break
                if not self.link_ready() or generation != self.link.get("generation"):
                    self.job.update(state="paused", message="IPTV 地址或取流进程已变化，丢弃本次检测结果")
                    break
                with self.lock:
                    if kind != "scan" or result["validation"] == "media" or key in self.state["observations"]:
                        self.observe(key, result)
                    if kind == "scan":
                        self.state["cursor"] += 1
                    completed = self.job["done"]
                    self.job["done"] = completed + 1
                    if kind == "audit":
                        audit = self.state["audit"]
                        audit["done"] = self.job["done"]
                        audit["counts"][result["validation"]] = audit["counts"].get(result["validation"], 0) + 1
                    try:
                        self.save()
                    except OSError:
                        self.job["done"] = completed
                        raise
                    results.append({"endpoint": key, **result})
                self.cancel.wait(2)
                if not self.drain():
                    self.job.update(state="paused", message="活动取流未结束或收到暂停请求，检查进度已保留")
                    break
            if self.job["state"] == "running":
                self.job["state"] = "complete"
        except Exception as exc:
            self.job.update(state="error", message=self.storage_error or str(exc)[:200])
        finally:
            try:
                with self.lock:
                    self.job["finished"] = time.time()
                    try:
                        if kind == "audit":
                            self.state["audit"].update({k: self.job[k] for k in ("state", "done", "finished")})
                            self.state["audit"]["message"] = self.job.get("message", "")
                        self.record(kind, before, {"results": results, "state": self.job["state"], "message": self.job.get("message", "")})
                    except Exception as exc:
                        self.job.update(state="error", message=self.storage_error or str(exc)[:200])
                        if kind == "audit" and "audit" in self.state:
                            # Preserve the last saved cursor, but allow an explicit retry.
                            self.state["audit"].update(state="error", message=self.job["message"])
            finally:
                self.job_lock.release()

    def export(self):
        with self.lock:
            lines = ["#EXTM3U"]
            for r in self.rows():
                if r["change"] not in ("possible_move", "new_media") or r.get("validation") != "media":
                    continue
                name = (r.get("name") or r["endpoint"]).replace("\n", " ").replace("\r", " ")
                lines.extend([f"#EXTINF:-1,{name}", f'http://{self.router}:12123/{r.get("protocol", "udp")}/{r["endpoint"]}'])
            return {"playlist": "\n".join(lines) + "\n", "rows": self.rows(), "history": self.state["history"]}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass

    def respond(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def dispatch(self):
        if self.path == "/health" and self.command == "GET":
            failed = bool(self.server.worker.storage_error)
            self.respond(503 if failed else 200, {"status": "storage_error" if failed else "ok"})
            return
        # Synology's Docker userland proxy replaces the source with its bridge gateway.
        # The scoped token remains mandatory; gateway acceptance is not a client-IP ACL.
        allowed = (self.server.worker.router, "127.0.0.1", getattr(self.server, "docker_gateway", ""))
        if self.client_address[0] not in allowed or not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), ("Bearer " + self.server.token).encode()):
            self.respond(403, {"error": "Forbidden"})
            return
        try:
            data = {}
            if self.command == "POST":
                size = int(self.headers.get("Content-Length", "0"))
                if not 1 <= size <= 1048576:
                    raise ValueError("Invalid request size")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
            worker = self.server.worker
            actions = {("GET", "/v1/status"): worker.status, ("GET", "/v1/export"): worker.export,
                       ("POST", "/v1/sample"): lambda: worker.ingest(data),
                       ("POST", "/v1/settings"): lambda: worker.settings(data),
                       ("POST", "/v1/scan"): lambda: worker.start("scan", data),
                       ("POST", "/v1/audit"): lambda: worker.start("audit", data),
                       ("POST", "/v1/pause"): worker.pause,
                       ("POST", "/v1/link"): lambda: worker.set_link(data),
                       ("POST", "/v1/verify"): lambda: worker.start("verify", data)}
            actions[("POST", "/v1/identify")] = lambda: worker.start("identify", data)
            actions[("POST", "/v1/name")] = lambda: worker.confirm_name(data)
            action = actions.get((self.command, self.path))
            if action is None:
                self.respond(404, {"error": "Not found"})
                return
            self.respond(200, action())
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            self.respond(400, {"error": str(exc)[:200]})
        except OSError:
            self.respond(503, {"error": "状态未保存，请检查服务存储空间和写入权限后重试"})

    do_GET = do_POST = dispatch


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    slots = threading.BoundedSemaphore(8)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


if __name__ == "__main__":
    os.umask(0o077)
    os.nice(10)
    token = Path(os.environ.get("TOKEN_FILE", "/run/secrets/api-token")).read_text().strip()
    if len(token) < 32:
        raise SystemExit("API token is required")
    server = BoundedServer(("0.0.0.0", 8080), Handler)
    server.token = token
    gateway = os.environ.get("DOCKER_GATEWAY", "")
    server.docker_gateway = str(ipaddress.ip_address(gateway)) if gateway else ""
    server.worker = Worker("/data/state.json", os.environ["ROUTER_IP"])
    server.serve_forever()
