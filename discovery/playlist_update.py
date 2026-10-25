"""Conservative playlist merge: local files only, no network or shell commands."""
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

from common import channel_name, confirmed_name, playlist, recent_checks, split_endpoint, successful_checks


PLAYLISTS = ("new.m3u", "new-logo.m3u", "new.txt")


def identity(name):
    name = re.sub(r"\s*线路\s*\d+\s*$", "", name).strip()
    name = re.sub(r"[（(].*?[）)]", "", name)
    name = re.sub(r"超高清|高清|超清|标清|频道|HD|LD", "", name, flags=re.I)
    return re.sub(r"[\s-]", "", name).casefold()


def category(name, vsp=False):
    # Same category priority as iptv-action:merge_vsp_playlist.
    if re.search(r"4K|超高清|UHD", name, re.I):
        return 1
    if re.match(r"(?:CCTV|央视|中央)[- ]*\d", name, re.I):
        return 2
    if re.search(r"(?:湖南|浙江|江苏|东方|北京|广东|深圳|山东|安徽|湖北|四川|重庆|河南|河北|辽宁|天津|福建|江西|贵州|云南|广西|陕西|黑龙江|吉林)卫视", name):
        return 3
    if "卫视" in name:
        return 4
    if re.search(r"CGTN|NHK|BBC|CNN|CNBC|半岛|法兰西|新加坡|欧洲|美国|亚洲|国际|英语|阿拉伯|俄罗斯|韩国|日本|WORLD|Macau|澳门", name, re.I):
        return 7
    return 5 if vsp else 6


def cctv_order(name):
    match = re.match(r"(?:CCTV|央视|中央)[- ]*(\d+)([+＋]?)", name, re.I)
    return int(match[1]) + (0.5 if match[2] else 0) if match else 999


def parse(text):
    header, entries, pending = [], [], []
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            if pending:
                raise ValueError("节目单含不完整条目，未更新")
            pending = [line]
        elif pending:
            pending.append(line)
            if line and not line.startswith("#"):
                info = playlist("\n".join(pending))
                entries.append({"lines": pending, "url": line, "name": pending[0].split(",", 1)[-1],
                                "endpoint": info[0]["endpoint"] if info else None})
                pending = []
        else:
            header.append(line)
    if pending or not entries or not header or not header[0].startswith("#EXTM3U"):
        raise ValueError("节目单格式异常，未更新")
    return header, entries


def render(header, entries):
    return "\n".join(header + [line for entry in entries for line in entry["lines"]]) + "\n"


def eligible(row, now):
    names = {p.get("name", "").strip() for p in row.get("programs", []) if p.get("name", "").strip()}
    if len(row.get("programs", [])) > 1:
        return None
    name = confirmed_name(row["endpoint"], row)
    if not name and len(names) == 1 and len(row.get("programs", [])) == 1:
        name = channel_name(next(iter(names)))
    if not name:
        return None
    if (row.get("validation") != "media" or row.get("last_check")
            or row.get("successes", 0) < 2
            or len(successful_checks(row["endpoint"], row, now)) < 2
            or not 0 <= now - row.get("last_success", 0) <= 7 * 86400
            or not row.get("streams") or row.get("protocol") not in ("udp", "rtp")):
        return None
    split_endpoint(row["endpoint"])
    return name


def plan(base_text, logo_text, observations, now=None, vsp_endpoints=(), logos=None):
    now = time.time() if now is None else now
    header, entries = parse(base_text)
    logo_header, logo_entries = parse(logo_text)
    # Do not overwrite a user's independent edits to one version of the list.
    if [(e["name"], e["url"]) for e in entries] != [(e["name"], e["url"]) for e in logo_entries]:
        raise ValueError("基础版与台标版条目不一致，等待人工核对")
    obs = {r["endpoint"]: r for r in observations}
    changes, pending = [], []
    source = next((urlsplit(e["url"]) for e in entries if e["endpoint"]), None)
    if source is None or source.scheme not in ("http", "https"):
        raise ValueError("未找到现有 udpxy 地址")
    logo_origin = ""
    for match in re.finditer(r'tvg-logo="(https?://[^"\s]+/iptv/logos/[^"\s]*)"', logo_text):
        parsed_logo = urlsplit(match[1])
        if parsed_logo.hostname == source.hostname:
            logo_origin = urlunsplit((parsed_logo.scheme, parsed_logo.netloc, "", "", ""))
            break
    for row in observations:
        name = eligible(row, now)
        key = row["endpoint"]
        if not name or key in {e["endpoint"] for e in entries}:
            continue
        matches = [i for i, e in enumerate(entries) if identity(e["name"]) == identity(name)]
        url = urlunsplit((source.scheme, source.netloc, f'/{row["protocol"]}/{key}', "", ""))
        if matches:
            if len(matches) != 1:
                pending.append({"endpoint": key, "name": name, "reason": "频道名称匹配不唯一"})
                continue
            index = matches[0]
            old_key = entries[index]["endpoint"]
            old = obs.get(old_key, {})
            failed = old.get("last_check", old.get("validation"))
            checks = recent_checks(old.get("daytime_failure_checks", []), now, spacing=3600, daytime=True)
            if not (failed == "no_response" and len(checks) >= 3 and checks[-1] - checks[0] >= 86400
                    and 0 <= now - old.get("last_failure", 0) <= 7 * 86400):
                pending.append({"endpoint": key, "name": name, "reason": "原源缺少三次白天无响应复核（跨度至少24小时），保留原频道"})
                continue
            for group in (entries, logo_entries):
                group[index]["lines"][-1] = url
                group[index].update(url=url, endpoint=key)
            changes.append({"kind": "replace", "name": entries[index]["name"], "before": old_key, "after": key})
        else:
            channel_id = re.sub(r"[\s-]", "", name)
            meta = f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}"'
            logo = (logos or {}).get(identity(name), "")
            if logo.startswith("/"):
                logo = logo_origin + logo if logo_origin else ""
            logo_meta = meta + (f' tvg-logo="{logo}"' if logo else "")
            entry = {"name": name, "url": url, "endpoint": key, "lines": [meta + "," + name, url]}
            logo_entry = dict(entry, lines=[logo_meta + "," + name, url])
            priority = category(name)
            index = len(entries)
            for i, existing in enumerate(entries):
                current = category(existing["name"], existing["endpoint"] in vsp_endpoints)
                if current > priority or (current == priority == 2 and cctv_order(existing["name"]) > cctv_order(name)):
                    index = i
                    break
            entries.insert(index, entry)
            logo_entries.insert(index, logo_entry)
            changes.append({"kind": "add", "name": name, "before": None, "after": key})
    if not changes:
        return {}, [], pending
    if len(changes) > 16:
        raise ValueError("单批变化超过16项，需人工核对后再更新")
    return {"new.m3u": render(header, entries), "new-logo.m3u": render(logo_header, logo_entries),
            "new.txt": "\n".join(e["name"] + "," + e["url"] for e in entries) + "\n"}, changes, pending


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_bytes(path, data, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix=".iptv-discovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_optional(path):
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def save_manifest(backup, manifest):
    atomic_bytes(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode(), 0o600)


def check_transactions(backup_root):
    # A prepared journal may represent a power loss, not a safe-to-retry update.
    for backup in sorted(backup_root.glob("update-*")):
        path = backup / "manifest.json"
        if not path.exists():
            continue  # The journal is saved before any production file is touched.
        try:
            manifest = json.loads(path.read_text())
            state = manifest.get("state") if isinstance(manifest, dict) else None
        except (OSError, ValueError) as exc:
            raise ValueError(f"更新记录无法读取，自动更新已暂停，请核对备份 {backup.name}") from exc
        if state not in ("applied", "rolled_back", "cancelled"):
            raise ValueError(f"存在未完成的节目单更新，自动更新已暂停，请核对备份 {backup.name}")


def rollback(www, old, new, attempted):
    conflicts, failures = [], []
    for name in reversed(attempted):
        path = www / name
        try:
            current = read_optional(path)
            if current == old[name]:
                continue
            if current != new[name]:
                conflicts.append(name)
                continue
            if old[name] is None:
                path.unlink()
                sync_directory(www)
            else:
                atomic_bytes(path, old[name])
        except OSError:
            failures.append(name)
    # Also detect external changes to files we had not reached yet.
    for name in PLAYLISTS:
        try:
            if read_optional(www / name) != old[name] and name not in conflicts + failures:
                conflicts.append(name)
        except OSError:
            if name not in failures:
                failures.append(name)
    return conflicts, failures


def apply(www, backup_root, rows):
    www, backup_root = Path(www), Path(backup_root)
    check_transactions(backup_root)
    old = {n: read_optional(www / n) for n in PLAYLISTS}
    if old["new.m3u"] is None or old["new-logo.m3u"] is None:
        raise ValueError("正式节目单缺失，未更新")
    vsp = set()
    if (www / "vsp.tsv").exists():
        for line in (www / "vsp.tsv").read_text().splitlines():
            fields = line.split("|")
            if len(fields) >= 5:
                vsp.add(fields[4])
    logos = {}
    for path in (www / "iptv/logos").glob("*"):
        if path.suffix.lower() in (".png", ".jpg", ".webp"):
            logos[identity(path.stem)] = "/iptv/logos/" + quote(path.name)
    files, changes, pending = plan(old["new.m3u"].decode(), old["new-logo.m3u"].decode(), rows,
                                   vsp_endpoints=vsp, logos=logos)
    if not files:
        return {"state": "unchanged", "changes": [], "pending": pending[:32]}
    # Keep backups instead of pruning user data. Stop safely at the explicit cap.
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    sync_directory(backup_root.parent)
    if len(list(backup_root.glob("update-*"))) >= 50:
        raise ValueError("已保留50次更新备份，请先归档备份")
    backup = backup_root / ("update-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    backup.mkdir(mode=0o700)
    sync_directory(backup_root)
    for name, data in old.items():
        if data is not None:
            atomic_bytes(backup / name, data, 0o600)
    new = {name: files[name].encode() for name in PLAYLISTS}
    manifest = {"version": 2, "at": time.time(), "changes": changes, "state": "prepared",
                "before": {n: hashlib.sha256(v).hexdigest() if v is not None else None for n, v in old.items()},
                "after": {n: hashlib.sha256(v).hexdigest() for n, v in new.items()}}
    save_manifest(backup, manifest)
    attempted = []
    try:
        for name in PLAYLISTS:
            expected = new | {n: old[n] for n in PLAYLISTS if n not in attempted}
            if any(read_optional(www / n) != expected[n] for n in PLAYLISTS):
                raise ValueError("节目单被其他任务修改，本次自动更新已取消")
            # A directory fsync can fail after rename; include that file in rollback.
            attempted.append(name)
            atomic_bytes(www / name, new[name])
        if any(read_optional(www / name) != new[name] for name in PLAYLISTS):
            raise ValueError("节目单回读不一致")
        manifest.update(state="applied")
        save_manifest(backup, manifest)
    except Exception as exc:
        if attempted:
            # Keep interruption during rollback distinguishable from a completed update.
            manifest.update(state="rolling_back")
            try:
                save_manifest(backup, manifest)
            except OSError as record_error:
                raise OSError(f"无法保存回退记录，未自动回退，请核对备份 {backup.name}") from record_error
        conflicts, failures = rollback(www, old, new, attempted) if attempted else ([], [])
        state = "needs_review" if conflicts or failures else "rolled_back" if attempted else "cancelled"
        manifest.update(state=state, conflicts=conflicts, rollback_failed=failures)
        try:
            save_manifest(backup, manifest)
        except OSError as record_error:
            raise OSError(f"节目单更新异常且结果记录未确认，自动更新已暂停，请核对备份 {backup.name}") from record_error
        if state == "needs_review":
            raise OSError(f"节目单更新异常，未覆盖外部改动；自动更新已暂停，请核对备份 {backup.name}") from exc
        raise
    return {"state": "applied", "at": time.time(), "changes": changes, "backup": str(backup), "pending": pending[:32]}
