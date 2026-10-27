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
    streams, programs, incomplete = [], [], 0

    def result(validation, reason):
        return {"validation": validation, "streams": streams, "programs": programs,
                "analysis": {"reason": reason,
                             "video_streams": sum(s["type"] == "video" for s in streams),
                             "audio_streams": sum(s["type"] == "audio" for s in streams),
                             "incomplete_streams": incomplete}}

    def positive_int(value):
        return type(value) is int and value > 0

    # Parsing is delegated to FFmpeg, with no network/file access from the demuxer.
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "pipe",
                               "-f", "mpegts", "-analyzeduration", "8000000" if deep else "1500000",
                               "-probesize", "8388608" if deep else "1048576",
                               "-show_programs", "-show_streams", "-of", "json", "pipe:0"],
                              input=data, capture_output=True, timeout=5 if deep else 3)
    except subprocess.TimeoutExpired:
        return result("inconclusive", "parser_timeout")
    except OSError:
        return result("inconclusive", "parser_unavailable")
    if proc.returncode != 0:
        return result("inconclusive", "parser_failed")
    if len(proc.stdout) > 262144:
        return result("inconclusive", "output_limit")
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        return result("inconclusive", "invalid_output")
    if (not isinstance(parsed, dict)
            or any(not isinstance(parsed.get(k, []), list) for k in ("streams", "programs"))):
        return result("inconclusive", "invalid_output")
    if any(len(parsed.get(k, [])) > 32 for k in ("streams", "programs")):
        return result("inconclusive", "output_limit")
    if (any(not isinstance(s, dict) for s in parsed.get("streams", []))
            or any(not isinstance(p, dict) or not isinstance(p.get("tags", {}), dict)
                   for p in parsed.get("programs", []))):
        return result("inconclusive", "invalid_output")
    programs = [{"id": str(p.get("program_id", ""))[:32],
                 "name": str(p.get("tags", {}).get("service_name", ""))[:160]}
                for p in parsed.get("programs", [])]
    for stream in parsed.get("streams", []):
        kind, codec = stream.get("codec_type"), stream.get("codec_name")
        if kind not in ("audio", "video"):
            continue
        valid = isinstance(codec, str) and codec not in ("", "unknown") and len(codec) <= 64
        if kind == "video":
            valid = valid and positive_int(stream.get("width")) and positive_int(stream.get("height"))
        else:
            rate = stream.get("sample_rate")
            if isinstance(rate, str) and rate.isascii() and rate.isdecimal() and len(rate) <= 8:
                rate = int(rate)
            valid = valid and positive_int(rate) and positive_int(stream.get("channels"))
        if not valid:
            incomplete += 1
            continue
        streams.append({"type": kind, "codec": codec, "width": stream.get("width"),
                        "height": stream.get("height")})
    # A codec label alone can be a guess from a truncated TS packet. A valid
    # audio track must not certify an announced video track with no dimensions.
    if incomplete:
        return result("inconclusive", "stream_parameters_incomplete")
    if not streams:
        return result("no_media", "no_av_streams")
    kinds = {s["type"] for s in streams}
    reason = "audio_video" if len(kinds) == 2 else "video_only" if "video" in kinds else "audio_only"
    return result("media", reason)


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
        for task in ("audit", "batch"):
            if self.state.get(task, {}).get("state") == "running":
                self.state[task].update(state="paused", message="服务重启，已保留检查进度")
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
                                  "capabilities": {"audit_modes": ["new", "resume"], "batch": True},
                                  "audit": {k: v for k, v in self.state.get("audit", {}).items() if k != "targets"},
                                  "batch": {k: v for k, v in self.state.get("batch", {}).items() if k != "targets"},
                                  "auto_followup": self.state.get("auto_followup", {}),
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
            auto_process = data.get("auto_process", self.state["settings"].get("auto_process", False))
            if type(auto_process) is not bool:
                raise ValueError("自动处理设置无效")
            new = {"active_enabled": data["active_enabled"], "ranges": [str(n) for n in networks], "ports": ports,
                   "auto_process": auto_process}
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
        deadline = started + (10 if deep else 4)
        stage, reason, response, transport = "connect", "eof", None, None

        def remaining():
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                raise TimeoutError()
            conn.timeout = timeout
            transport.settimeout(timeout)

        def result(validation, **fields):
            return {"validation": validation, "bytes_checked": len(body), "protocol": protocol,
                    "check_method": "deep" if deep else "short", **fields,
                    "last_probe": {"stage": stage, "reason": reason, "bytes_checked": len(body),
                              "elapsed_ms": round((time.monotonic() - started) * 1000),
                              "http_status": response.status if response is not None else None}}

        try:
            if self.cancel.is_set():
                reason = "cancelled"
                return result("inconclusive")
            conn.connect()
            transport = conn.sock
            # One receive deadline includes request, headers and body. The short
            # TCP connect timeout must not truncate multicast startup to one second.
            stage = "request"
            remaining()
            conn.request("GET", f"/{protocol}/{group}:{port}")
            stage = "headers"
            remaining()
            response = conn.getresponse()
            if response.status != 200:
                reason = "http_status"
                return result("no_media", http_status=response.status)
            stage = "body"
            limit = 8388608 if deep else 1048576
            while len(body) < limit:
                if self.cancel.is_set():
                    reason = "cancelled"
                    return result("inconclusive")
                remaining()
                chunk = response.read1(min(65536, limit - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            else:
                reason = "byte_limit"
        except TimeoutError:
            reason = "timeout"
            if not body:
                return result("no_response" if stage in ("headers", "body") else "inconclusive")
        except (OSError, http.client.HTTPException):
            reason = "transport_error"
            if not body:
                return result("inconclusive")
        finally:
            if response is not None:
                response.close()
            conn.close()
        try:
            details = media_details(bytes(body), deep=deep)
            analysis = details.pop("analysis", None)
            outcome = result(**details)
            if analysis is not None:
                outcome["last_probe"]["analysis"] = analysis
            return outcome
        except (subprocess.TimeoutExpired, ValueError):
            stage, reason = "analysis", "analysis_incomplete"
            return result("inconclusive")

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
                    mode = data.get("mode", "auto")
                    if mode not in ("auto", "new", "resume"):
                        raise ValueError("全量检查方式无效")
                    resumable = old.get("state") in ("paused", "error") and old.get("done", 0) < old.get("total", 0)
                    if mode == "resume" and (scheduled or not resumable):
                        raise ValueError("没有可继续的全量进度，请刷新后选择重新全量扫描")
                    if not scheduled and mode != "new" and resumable:
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
                                               "generation": self.link.get("generation"),
                                               "trigger": "scheduled" if scheduled else "manual"}
                        if old:
                            self.state["audit"]["previous"] = {
                                k: copy.deepcopy(old[k]) for k in
                                ("state", "done", "total", "started", "finished", "counts") if k in old}
                    self.state["audit"].update(state="running", message="")
                    self.save()
                elif kind == "batch":
                    old = self.state.get("batch", {})
                    if data.get("trigger") == "auto":
                        if not self.state["settings"].get("auto_process"):
                            raise ValueError("自动处理已关闭")
                        if old.get("state") in ("paused", "error") and old.get("done", 0) < old.get("total", 0):
                            raise ValueError("上次批量处理尚未完成，请先继续该任务")
                    mode = data.get("mode", "new")
                    if mode not in ("new", "resume"):
                        raise ValueError("批量处理方式无效")
                    if mode == "resume":
                        if old.get("state") not in ("paused", "error") or old.get("done", 0) >= old.get("total", 0):
                            raise ValueError("没有可继续的批量任务")
                        targets = old["targets"]
                    else:
                        workflow = data.get("workflow", "smart")
                        if workflow not in ("smart", "verify", "identify"):
                            raise ValueError("批量处理类型无效")
                        keys = data.get("endpoints", [])
                        limit = 8 if workflow == "identify" else 32
                        if not isinstance(keys, list) or not 1 <= len(keys) <= limit or any(not isinstance(k, str) for k in keys):
                            raise ValueError(f"本次最多处理{limit}个已记录地址")
                        allowed = {r["endpoint"] for r in self.state["baseline"]} | self.state["observations"].keys()
                        if len(set(keys)) != len(keys) or any(k not in allowed for k in keys):
                            raise ValueError("只能选择不重复的已记录地址")
                        stages = ["verify", "identify", "recheck"] if workflow == "smart" else [workflow]
                        targets = [{"endpoint": k, "stage": stage} for stage in stages for k in keys]
                        self.state["batch"] = {"targets": targets, "done": 0, "total": len(targets),
                                               "addresses": len(keys), "workflow": workflow, "counts": {},
                                               "started": time.time(), "trigger": data.get("trigger") if data.get("trigger") == "auto" else "manual"}
                        if old:
                            self.state["batch"]["previous"] = {k: copy.deepcopy(old[k]) for k in
                                ("state", "done", "total", "started", "finished", "counts") if k in old}
                    self.state["batch"].update(state="running", message="")
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
                if kind in ("audit", "batch"):
                    self.job["done"] = self.state[kind]["done"]
            threading.Thread(target=self.run, args=(kind, targets), daemon=True).start()
        except Exception:
            try:
                with self.lock:
                    if self.job.get("state") == "running":
                        self.job.update(state="error", message="任务未能启动，可以稍后重试")
                        if kind in ("audit", "batch") and self.state.get(kind, {}).get("state") == "running":
                            self.state[kind].update(state="paused", message=self.job["message"])
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
        item.pop("last_probe", None)
        if isinstance(result.get("last_probe"), dict):
            item["last_probe"] = result["last_probe"]
        if result["validation"] == "media":
            fingerprint = media_fingerprint(key, result)
            checks = recent_checks(item.get("success_checks", []), now) if item.get("success_fingerprint") == fingerprint else []
            if not checks or now - checks[-1] >= 60:
                checks.append(now)
            checks = checks[-2:]
            item.update(success_fingerprint=fingerprint, success_checks=checks,
                        successes=len(checks), first_success=checks[0], daytime_failure_checks=[],
                        archive_failure_checks=[])
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
            checks = recent_checks(item.get("daytime_failure_checks", []), now, spacing=3600,
                                   daytime=True, max_age=30 * 86400, daytime_end=22)
            if result["validation"] == "no_response":
                checks = recent_checks(checks + [now], now, spacing=3600,
                                       daytime=True, max_age=30 * 86400, daytime_end=22)
            else:
                checks = []  # Inconclusive/media-without-streams is not a silent-channel streak.
                item["archive_failure_checks"] = []
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

    def record_archive_evidence(self):
        """Only complete, healthy control rounds can accumulate retirement evidence."""
        audit, now = self.state.get("audit", {}), time.time()
        if (audit.get("state") != "complete" or not audit.get("total")
                or audit.get("done") != audit.get("total") or not self.link_ready()
                or not audit.get("generation") or audit["generation"] != self.link.get("generation")
                or not 0 <= now - audit.get("started", 0) <= 86400):
            return
        keys = [r["endpoint"] for r in self.state["baseline"]]
        rows = self.state["observations"]
        if (not keys or len(set(keys)) != len(keys)
                or any(k not in rows or not audit["started"] <= rows[k].get("checked_at", 0) <= now for k in keys)):
            return
        checks = {k: rows[k].get("last_check") or rows[k].get("validation") for k in keys}
        healthy = [k for k in keys if checks[k] == "media"]
        if (any(s not in ("media", "no_response") for s in checks.values())
                or len(healthy) < 2 or (len(keys) - len(healthy)) / len(keys) > .20):
            return
        for key in keys:
            if checks[key] != "no_response":
                continue
            group, port = split_endpoint(key)
            subnet = ipaddress.ip_network(group + "/24", strict=False)
            if not any(split_endpoint(k)[1] == port and ipaddress.ip_address(split_endpoint(k)[0]) in subnet for k in healthy):
                continue
            item = rows[key]
            stamps = item.get("archive_failure_checks", []) + [item["checked_at"]]
            item["archive_failure_checks"] = recent_checks(stamps, now, spacing=3600, daytime=True,
                                                           max_age=30 * 86400, daytime_end=22)[-64:]

    def run(self, kind, targets):
        before, results, followup = {}, [], []
        try:
            with self.lock:
                before = {r["endpoint"]: r for r in self.rows()}
            for target in targets[self.job.get("done", 0):]:
                key = target["endpoint"] if kind == "batch" else target
                stage = target["stage"] if kind == "batch" else kind
                skip = False
                if kind == "batch" and self.state["batch"]["workflow"] == "smart" and stage != "verify":
                    row = next((r for r in self.rows() if r["endpoint"] == key), {})
                    skip = row.get("validation") != "media" or bool(row.get("last_check"))
                    if stage == "identify":
                        # A short probe can see an SDT name but miss it on the next
                        # sample. New stream-named candidates still need the same
                        # deep evidence on both sides of the spaced recheck.
                        skip = skip or row.get("name_source") in ("playlist", "manual") or len(row.get("programs", [])) > 1
                    elif not skip:
                        wait = max(0, row.get("last_success", 0) + 61 - time.time())
                        if wait > 61:
                            raise ValueError("验证时间异常，请核对设备时钟")
                        self.job["message"] = "等待二次验证间隔，不占用取流连接"
                        self.cancel.wait(wait)
                if self.cancel.is_set() or time.time() - self.job["started"] >= 3600:
                    self.job.update(state="paused", message="检查已暂停，进度已保留")
                    break
                if not self.link_ready():
                    self.job.update(state="paused", message="IPTV 入口状态异常或过期，未判定频道失效")
                    break
                if not skip and self.busy():
                    self.job.update(state="paused", message="检测到活动取流，已暂停；可空闲时继续")
                    break
                self.job.pop("message", None)
                generation = self.link.get("generation")
                deep = stage == "identify" or (stage == "recheck" and self.state["observations"].get(key, {}).get("check_method") == "deep")
                result = {"validation": "skipped"} if skip else self.probe(key, deep=True) if deep else self.probe(key)
                if self.cancel.is_set():
                    self.job.update(state="paused", message="检查已取消，本次结果未写入")
                    break
                if not self.link_ready() or generation != self.link.get("generation"):
                    self.job.update(state="paused", message="IPTV 地址或取流进程已变化，丢弃本次检测结果")
                    break
                with self.lock:
                    if not skip and (kind != "scan" or result["validation"] == "media" or key in self.state["observations"]):
                        self.observe(key, result)
                    if kind == "scan":
                        self.state["cursor"] += 1
                    completed = self.job["done"]
                    self.job["done"] = completed + 1
                    if kind in ("audit", "batch"):
                        audit = self.state[kind]
                        audit["done"] = self.job["done"]
                        audit["counts"][result["validation"]] = audit["counts"].get(result["validation"], 0) + 1
                    try:
                        self.save()
                    except OSError:
                        self.job["done"] = completed
                        raise
                    results.append({"endpoint": key, **result})
                if not skip:
                    self.cancel.wait(2)
                if not skip and not self.drain():
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
                        if kind in ("audit", "batch"):
                            self.state[kind].update({k: self.job[k] for k in ("state", "done", "finished")})
                            self.state[kind]["message"] = self.job.get("message", "")
                        if kind == "audit":
                            self.record_archive_evidence()
                        details = {"results": results, "state": self.job["state"], "message": self.job.get("message", "")}
                        if kind in ("audit", "batch"):
                            details["trigger"] = self.state[kind].get("trigger", "manual")
                        if kind == "batch" and self.state[kind].get("trigger") == "auto":
                            self.state["auto_followup"] = {
                                "state": self.job["state"], "count": self.state[kind]["addresses"],
                                "done": self.job["done"], "total": self.job["total"],
                                "message": self.job.get("message", ""), "at": time.time()}
                        self.record(kind, before, details)
                        if kind == "audit" and self.job["state"] == "complete" and self.state["settings"].get("auto_process"):
                            followup = [r["endpoint"] for r in self.rows() if r["change"] != "existing"
                                        and r.get("validation") == "media" and not r.get("last_check")
                                        and (r.get("verified_count", 0) < 2 or r.get("name_source") in ("unknown", "catalog"))][:32]
                    except Exception as exc:
                        self.job.update(state="error", message=self.storage_error or str(exc)[:200])
                        if kind in ("audit", "batch") and kind in self.state:
                            # Preserve the last saved cursor, but allow an explicit retry.
                            self.state[kind].update(state="error", message=self.job["message"])
            finally:
                self.job_lock.release()
        if followup:
            # One bounded follow-up, never overwrite an unfinished batch or retry in a loop.
            try:
                self.start("batch", {"workflow": "smart", "endpoints": followup, "trigger": "auto"})
                result = {"state": "submitted", "count": len(followup), "message": "自动识别与复核已提交"}
            except (ValueError, OSError, RuntimeError) as exc:
                result = {"state": "blocked", "count": len(followup), "message": str(exc)[:200]}
            with self.lock:
                batch = self.state.get("batch", {})
                # The worker may finish before start() returns. Do not replace a
                # completed/paused outcome with the earlier submission status.
                if result["state"] == "submitted" and batch.get("trigger") == "auto":
                    result.update(state=batch.get("state", "submitted"), done=batch.get("done", 0),
                                  total=batch.get("total", 0), message=batch.get("message", ""))
                self.state["auto_followup"] = {**result, "at": time.time()}
                try:
                    self.save()
                except OSError:
                    pass  # storage_error remains visible and blocks playlist updates.

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
                       ("POST", "/v1/batch"): lambda: worker.start("batch", data),
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
