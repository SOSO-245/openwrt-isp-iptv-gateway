#!/usr/bin/python3
"""Bounded passive sampling and an authenticated gateway to the NAS worker."""
import argparse
import datetime as dt
import fcntl
import hmac
import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from common import atomic_json, packet_endpoints, playlist
from playlist_update import apply as apply_playlist

CONFIG = Path("/etc/iptv-discovery.json")
RUNTIME = Path("/tmp/iptv-discovery")
STAMP = Path("/etc/iptv-discovery-last-day")
WWW = Path("/www")
SHANGHAI = dt.timezone(dt.timedelta(hours=8))
STOP = False
CAPTURE = None


def config():
    c = json.loads(CONFIG.read_text())
    ip = ipaddress.ip_address(c["nas_host"])
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_multicast:
        raise ValueError("NAS must use a private LAN address")
    if not 1 <= int(c.get("nas_port", 18086)) <= 65535 or len(c["token"]) < 32:
        raise ValueError("Invalid NAS connection settings")
    if type(c.get("enabled")) is not bool:
        raise ValueError("Invalid enabled setting")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", c.get("daily_time", "")):
        raise ValueError("Invalid daily time")
    c.setdefault("weekly_enabled", False)
    c.setdefault("weekly_day", 4)
    c.setdefault("weekly_time", "03:30")
    c.setdefault("weekdays_only", False)
    c.setdefault("auto_update", False)
    validate_schedule(c)
    return c


def validate_schedule(c):
    if (type(c.get("weekly_enabled")) is not bool or type(c.get("auto_update")) is not bool
            or type(c.get("weekdays_only", False)) is not bool
            or type(c.get("weekly_day")) is not int or not 0 <= c["weekly_day"] <= 6
            or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", c.get("weekly_time", ""))):
        raise ValueError("每周计划或自动更新设置无效")


def nas_request(c, path, data=None):
    conn = http.client.HTTPConnection(c["nas_host"], int(c.get("nas_port", 18086)), timeout=8)
    try:
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        conn.request("POST" if body is not None else "GET", path, body,
                     {"Authorization": "Bearer " + c["token"], "Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("NAS response too large")
        result = json.loads(raw)
        if response.status != 200:
            raise ValueError(result.get("error", "NAS request failed"))
        return result
    finally:
        conn.close()


def capture_interface():
    name = Path("/etc/iptv-input-iface").read_text().strip()
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", name):
        raise ValueError("IPTV 入口名称无效")
    # Refuse silent fallback to LAN or the now-missing set-top-box USB interface.
    if not Path("/sys/class/net/br-iptv/brif", name).exists():
        raise ValueError("IPTV 入口不在 br-iptv 中；未修改网络")
    if Path("/sys/class/net", name, "carrier").read_text().strip() != "1":
        raise ValueError("IPTV 入口未连接；未修改网络")
    return name


def capture(name):
    global CAPTURE
    cmd = ["tcpdump", "-p", "-nn", "-q", "-l", "-i", name, "-s", "96", "-c", "600",
           "ip and udp and dst net 239.0.0.0/8"]
    CAPTURE = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        try:
            out, err = CAPTURE.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            CAPTURE.send_signal(signal.SIGINT)
            try:
                out, err = CAPTURE.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                CAPTURE.kill()
                out, err = CAPTURE.communicate(timeout=2)
        if CAPTURE.returncode not in (0, -signal.SIGINT) and not out:
            raise RuntimeError("tcpdump 无法采样；请检查接口与权限")
        if STOP:
            raise RuntimeError("采样已停止")
        return packet_endpoints(out.decode("ascii", "replace"))
    finally:
        if CAPTURE.poll() is None:
            CAPTURE.kill()
            CAPTURE.wait(timeout=2)
        CAPTURE = None


def baseline():
    return playlist((WWW / "new.m3u").read_text(errors="replace"))


def catalog(path=Path("/www/vsp.m3u")):
    # Existing local cache only: do not fetch the operator portal or STB session.
    if not path.exists() or path.stat().st_size > 2097152:
        return {"rows": [], "at": 0}
    return {"rows": [{k: r.get(k, "") for k in ("endpoint", "protocol", "name")}
                     for r in playlist(path.read_text(errors="replace"))], "at": path.stat().st_mtime}


def baseline_report():
    return {"baseline": baseline(), "catalog": catalog(), "observations": [], "trigger": "baseline"}


@contextmanager
def playlist_guard():
    # Share reconcile's lock across the snapshot, NAS acknowledgement and job launch.
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / "update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("节目单正在更新或同步，请稍后重试") from exc
        yield


def submit_check(c, action, data):
    with playlist_guard():
        nas_request(c, "/v1/link", link_status())
        # All checks must use the current list, including manual scan/verify.
        nas_request(c, "/v1/sample", baseline_report())
        return nas_request(c, "/v1/" + action, data)


def sample(trigger="manual"):
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / "sample.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"message": "已有采样任务运行"}
        status = {"state": "sampling", "started": time.time(), "trigger": trigger}
        atomic_json(RUNTIME / "status.json", status)
        try:
            c = config()
            name = capture_interface()
            if not baseline():
                raise ValueError("正式节目单为空，停止采样")
            observed = capture(name)
            with playlist_guard():
                report = baseline_report()
                if not report["baseline"]:
                    raise ValueError("正式节目单已变化或为空，采样结果未提交")
                report.update(version=1, observations=observed, interface=name,
                              sampled_at=time.time(), trigger=trigger)
                result = nas_request(c, "/v1/sample", report)
            status.update(state="complete", count=len(observed), result=result, interface=name)
        except Exception as exc:
            status.update(state="error", error=str(exc))
        status["finished"] = time.time()
        atomic_json(RUNTIME / "status.json", status)
        return status


def due(now, last_day, daily_time, weekdays_only=False):
    local = dt.datetime.fromtimestamp(now, SHANGHAI)
    if weekdays_only and local.weekday() >= 5:
        return False
    return local.strftime("%H:%M") == daily_time and local.date().isoformat() != last_day


def due_weekly(now, last_day, weekday, clock):
    return dt.datetime.fromtimestamp(now, SHANGHAI).weekday() == weekday and due(now, last_day, clock)


def next_runs(c, marks, now):
    local = dt.datetime.fromtimestamp(now, SHANGHAI).replace(second=0, microsecond=0)
    result = {}
    for offset in range(9):
        day = local.date() + dt.timedelta(days=offset)
        for kind, enabled, clock, marker in (
            ("sample", c.get("enabled"), c.get("daily_time", "09:30"), "day"),
            ("audit", c.get("weekly_enabled"), c.get("weekly_time", "09:30"), "weekly"),
        ):
            if not enabled or kind in result or marks.get(marker) == day.isoformat():
                continue
            if kind == "sample" and c.get("weekdays_only") and day.weekday() >= 5:
                continue
            if kind == "audit" and day.weekday() != c.get("weekly_day", 4):
                continue
            if (kind == "sample" and c.get("weekly_enabled") and day.weekday() == c.get("weekly_day", 4)
                    and clock == c.get("weekly_time")):
                continue
            hour, minute = map(int, clock.split(":"))
            planned = dt.datetime.combine(day, dt.time(hour, minute), SHANGHAI)
            if planned >= local:
                result[kind] = planned.timestamp()
    return result


def schedule_tick(c, now=None):
    now = time.time() if now is None else now
    marks = json.loads(STAMP.read_text()) if STAMP.exists() else {}
    weekly = c["weekly_enabled"] and due_weekly(now, marks.get("weekly", ""), c["weekly_day"], c["weekly_time"])
    daily = c["enabled"] and due(now, marks.get("day", ""), c["daily_time"], c["weekdays_only"])
    if not weekly and not daily:
        nas_request(c, "/v1/link", link_status())
        return

    today = dt.datetime.fromtimestamp(now, SHANGHAI).date().isoformat()
    kind = "audit" if weekly else "sample"
    if daily:
        marks["day"] = today
    if weekly:
        marks["weekly"] = today
    attempt = {"at": now, "state": "unconfirmed", "message": "尚未收到完成确认，请刷新核对任务；不会自动重复提交"}
    marks.setdefault("runs", {})[kind] = attempt
    # Claim before any network work, including failure to reach the NAS.
    atomic_json(STAMP, marks)
    stage = "link"
    try:
        nas_request(c, "/v1/link", link_status())
        if weekly:
            with playlist_guard():
                stage = "baseline"
                nas_request(c, "/v1/sample", baseline_report())
                stage = "submit"
                nas_request(c, "/v1/audit", {"trigger": "scheduled"})
            attempt.update(state="submitted", message="全量任务已提交，完成情况见检查进度")
        else:
            stage = "sample"
            result = sample("scheduled")
            if result.get("state") == "complete":
                attempt.update(state="complete", message="短采样已完成", count=result.get("count", 0))
            elif result.get("state") == "error":
                attempt.update(state="error", message="短采样未完成，请查看采样提示后手动重试")
            else:
                attempt.update(state="skipped", message="已有采样运行，本次计划未重复启动")
    except (OSError, http.client.HTTPException):
        attempt.update(state="unconfirmed" if stage == "submit" else "error",
                       message="未收到提交确认，请刷新核对全量任务；不会自动重试" if stage == "submit"
                       else "NAS通信失败，本次计划未完成；恢复后可手动执行")
    except Exception:
        attempt.update(state="unconfirmed" if stage == "submit" else "error",
                       message="全量提交未确认，请刷新核对任务状态" if stage == "submit"
                       else "计划准备或执行失败，请检查服务状态后手动重试")
    attempt["finished"] = time.time()
    atomic_json(STAMP, marks)


def dashboard_status(c):
    try:
        result = nas_request(c, "/v1/status")
    except (OSError, ValueError, http.client.HTTPException):
        result = {"service_error": "NAS扫描服务暂不可用；已有结果可能过期，仅本地计划可读取"}
    defaults = {"weekdays_only": False, "weekly_enabled": False, "weekly_day": 4,
                "weekly_time": "03:30", "auto_update": False}
    result["collector"] = {"enabled": c["enabled"], "daily_time": c["daily_time"],
                           **{k: c.get(k, v) for k, v in defaults.items()}}
    result["collector"]["timezone"] = "Asia/Shanghai"
    result["capture"] = json.loads((RUNTIME / "status.json").read_text()) if (RUNTIME / "status.json").exists() else {"state": "waiting"}
    result["update"] = json.loads((RUNTIME / "update.json").read_text()) if (RUNTIME / "update.json").exists() else {}
    marks = json.loads(STAMP.read_text()) if STAMP.exists() else {}
    result["scheduled_runs"] = marks.get("runs", {})
    result["next_runs"] = next_runs(c, marks, time.time())
    return result


def udpxy_master(proc=Path("/proc")):
    processes = {}
    for path in proc.glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().decode().split("\0")
            if (Path(args[0]).name != "udpxy" or "-m" not in args or "-p" not in args
                    or args[args.index("-p") + 1] != "12123"):
                continue
            parent = re.search(r"^PPid:\s*(\d+)", (path.parent / "status").read_text(), re.M)
            if parent:
                processes[path.parent.name] = (parent[1], args[args.index("-m") + 1])
        except (OSError, ValueError, IndexError):
            continue
    # Forked clients inherit the master's argv; their PID must not identify the service.
    masters = [(pid, source) for pid, (parent, source) in processes.items() if parent not in processes]
    return masters[0] if len(masters) == 1 else None


def link_status():
    status = {"ready": False, "generation": "", "message": "IPTV 入口尚未就绪"}
    try:
        name = capture_interface()
        output = subprocess.check_output(["ip", "-4", "-o", "addr", "show", "br-iptv"], timeout=2).decode()
        match = re.search(r"\binet ([0-9.]+)/", output)
        if not match:
            return status
        current_ip = match[1]
        master = udpxy_master()
        if master:
            pid, source = master
            status.update(ready=source == current_ip, generation=current_ip + ":" + pid,
                          message="入口正常" if source == current_ip else "IPTV 地址已变化，等待取流服务同步",
                          interface=name)
    except Exception as exc:
        status["message"] = str(exc)[:160]
    return status


def legacy_busy():
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = path.read_bytes().split(b"\0")
            if any(a in (b"/www/cgi-bin/iptv-action", b"/www/cgi-bin/iptv-action-logo-helper") for a in args[:3]):
                return True
        except OSError:
            continue
    return False


def sync_applied(c, result):
    # Keep the successful local write separate from the downstream acknowledgement.
    result.update(sync_pending=True, sync_attempt_at=time.time())
    atomic_json(RUNTIME / "update.json", result)
    try:
        nas_request(c, "/v1/sample", baseline_report())
    except (OSError, ValueError, http.client.HTTPException):
        result["sync_message"] = "节目单已更新，NAS同步未确认；稍后仅重试同步"
        atomic_json(RUNTIME / "update.json", result)
        return False
    result.update(sync_pending=False, synced_at=time.time())
    result.pop("sync_message", None)
    atomic_json(RUNTIME / "update.json", result)
    return True


def reconcile(c):
    if not c.get("auto_update") or legacy_busy():
        return
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with (RUNTIME / "update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        previous_file = RUNTIME / "update.json"
        previous = json.loads(previous_file.read_text()) if previous_file.exists() else {}
        status = nas_request(c, "/v1/status")
        if status.get("storage_error"):
            if not previous.get("sync_pending"):
                atomic_json(previous_file, {**previous, "state": "error", "message": "NAS状态保存异常，自动更新已暂停"})
            return
        if status.get("job", {}).get("state") == "running":
            return
        if previous.get("sync_pending"):
            if time.time() - previous.get("sync_attempt_at", 0) >= 300:
                sync_applied(c, previous)
            return
        rows = status.get("rows", [])
        key = hashlib.sha256(json.dumps(rows, sort_keys=True).encode() + (WWW / "new.m3u").read_bytes()
                             + (WWW / "new-logo.m3u").read_bytes()).hexdigest()
        marker = RUNTIME / "update-attempt.json"
        prior = json.loads(marker.read_text()) if marker.exists() else {}
        if prior.get("key") == key and (prior.get("ok") or time.time() - prior.get("at", 0) < 300):
            return
        try:
            result = apply_playlist(WWW, "/etc/iptv-discovery-backups", rows)
        except Exception as exc:
            atomic_json(previous_file, {**previous, "state": "error", "message": str(exc)[:200]})
            atomic_json(marker, {"key": key, "at": time.time(), "ok": False})
            return
        if result["state"] == "applied":
            result["last_applied"] = {k: result[k] for k in ("at", "changes", "backup")}
            if not sync_applied(c, result):
                return
        else:
            if previous.get("last_applied"):
                result["last_applied"] = previous["last_applied"]
            atomic_json(previous_file, result)
        atomic_json(marker, {"key": key, "at": time.time(), "ok": True})


def shutdown(_signum, _frame):
    global STOP
    STOP = True
    if CAPTURE is not None and CAPTURE.poll() is None:
        CAPTURE.send_signal(signal.SIGINT)


def daemon():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    while not STOP:
        try:
            c = config()
            schedule_tick(c)
            reconcile(c)
        except Exception as exc:
            print("IPTV discovery scheduler: " + str(exc), file=sys.stderr)
        for _ in range(15):
            if STOP:
                return
            time.sleep(1)


def cgi():
    code = 200
    try:
        if os.environ.get("REQUEST_METHOD") != "POST":
            raise PermissionError("POST required")
        # uhttpd does not forward arbitrary X-* headers to CGI. JSON POSTs cannot
        # be submitted by a cross-origin HTML form; credentials remain required.
        if os.environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
            raise PermissionError("仅接受 JSON 请求")
        length = int(os.environ.get("CONTENT_LENGTH", "0"))
        if not 1 <= length <= 16384:
            raise ValueError("Invalid request size")
        request = json.loads(sys.stdin.buffer.read(length))
        user, password = request.get("user", ""), request.get("password", "")
        if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
            raise PermissionError("未登录")
        if any(ch in user + password for ch in "\r\n") or ":" in user:
            raise PermissionError("未登录")
        credential = f"{user}:{password}"
        accounts = Path("/etc/soso-iptv-auth").read_text().splitlines()
        if not any(hmac.compare_digest(credential.encode(), line.encode()) for line in accounts if line):
            raise PermissionError("未登录或账号密码错误")
        c = config()
        action = request.get("action", "status")
        if action == "status":
            result = dashboard_status(c)
        elif action == "sample":
            RUNTIME.mkdir(mode=0o700, exist_ok=True)
            with (RUNTIME / "launch.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                stamp = RUNTIME / "launch.json"
                previous = json.loads(stamp.read_text()).get("at", 0) if stamp.exists() else 0
                if time.time() - previous < 15:
                    raise ValueError("采样已提交，请稍候刷新")
                atomic_json(stamp, {"at": time.time()})
            subprocess.Popen([sys.executable, __file__, "--once"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            result = {"message": "采样任务已提交"}
        elif action == "settings":
            opts = request.get("settings", {})
            if type(opts.get("enabled")) is not bool or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", opts.get("daily_time", "")):
                raise ValueError("定时设置无效")
            validate_schedule(opts)
            result = nas_request(c, "/v1/settings", opts)
            c.update(enabled=opts["enabled"], daily_time=opts["daily_time"])
            c["weekdays_only"] = opts.get("weekdays_only", False)
            c.update({k: opts[k] for k in ("weekly_enabled", "weekly_day", "weekly_time", "auto_update")})
            atomic_json(CONFIG, c)
        elif action in ("scan", "verify", "audit", "pause", "identify"):
            payload = {"endpoints": request.get("endpoints", []), "retry_failed": request.get("retry_failed") is True}
            result = nas_request(c, "/v1/pause", payload) if action == "pause" else submit_check(c, action, payload)
        elif action == "catalog":
            with playlist_guard():
                nas_request(c, "/v1/sample", baseline_report())
            result = {"message": "本地频道表已匹配；历史缓存名称仍需人工确认"}
        elif action == "name":
            with playlist_guard():
                nas_request(c, "/v1/sample", baseline_report())
                result = nas_request(c, "/v1/name", {k: request.get(k) for k in
                                                   ("endpoint", "name", "fingerprint", "reviewed", "revoke")})
        elif action == "export":
            result = nas_request(c, "/v1/export")
        else:
            raise ValueError("Unknown action")
    except PermissionError as exc:
        code, result = 403, {"error": str(exc)}
    except Exception as exc:
        code, result = 400, {"error": str(exc)}
    print(f"Status: {code}\r\nContent-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cgi", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.cgi:
        cgi()
    elif args.once:
        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        os.nice(10)
        print(json.dumps(sample(), ensure_ascii=False))
    else:
        os.nice(10)
        daemon()
