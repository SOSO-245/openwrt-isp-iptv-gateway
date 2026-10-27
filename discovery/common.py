"""Small, shared data model. No raw captures or account data leave the router."""
import hashlib
import datetime as dt
import ipaddress
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".discovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def endpoint(group, port):
    addr = ipaddress.IPv4Address(group)
    port = int(port)
    # Only the configured IPTV administrative multicast scope, never LAN discovery.
    if addr not in ipaddress.IPv4Network("239.0.0.0/8") or not 1 <= port <= 65535:
        raise ValueError("Invalid IPTV multicast endpoint")
    return f"{addr}:{port}"


def split_endpoint(value):
    group, port = value.split(":")
    endpoint(group, port)
    return group, int(port)


def playlist(text):
    rows, meta = [], {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            attrs = dict(re.findall(r'([\w-]+)="([^"\r\n]*)"', line))
            meta = {"name": line.split(",", 1)[-1][:160],
                    "channel_id": attrs.get("tvg-id", "")[:160]}
        elif line and not line.startswith("#"):
            parsed = urlsplit(line)
            match = re.fullmatch(r"/(udp|rtp)/(\d+\.\d+\.\d+\.\d+):(\d+)/?", parsed.path)
            if match:
                try:
                    key = endpoint(match[2], match[3])
                    rows.append(dict(meta, endpoint=key, protocol=match[1]))
                except ValueError:
                    pass
            meta = {}
    return rows[:4096]


def packet_endpoints(text):
    result = {}
    pattern = r"\bIP (\d+\.\d+\.\d+\.\d+)\.\d+ > (\d+\.\d+\.\d+\.\d+)\.(\d+): UDP"
    for src, group, port in re.findall(pattern, text):
        try:
            ipaddress.IPv4Address(src)
            key = endpoint(group, port)
        except ValueError:
            continue
        item = result.setdefault(key, {"endpoint": key, "packets": 0})
        item["packets"] += 1
    return list(result.values())[:600]


def multicast_ranges(values, baseline):
    allowed = {str(ipaddress.ip_network(split_endpoint(r["endpoint"])[0] + "/24", strict=False))
               for r in baseline}
    networks = []
    for value in values:
        network = ipaddress.ip_network(value, strict=True)
        if network.version != 4 or network.prefixlen < 24:
            raise ValueError("扫描范围最多为 /24")
        if not any(network.subnet_of(ipaddress.ip_network(n)) for n in allowed):
            raise ValueError("扫描范围必须属于现有节目单的 /24 网段")
        if network not in networks:
            networks.append(network)
    if sum(n.num_addresses for n in networks) > 256:
        raise ValueError("每次配置最多包含 256 个地址")
    return networks


def channel_name(value):
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if (not name or len(name) > 120 or re.search(r'[\x00-\x1f\x7f<>"|,\u2028\u2029]', name)
            or re.fullmatch(r"(?:service|program|channel|test|unknown|节目|频道|名称待识别)[ _-]*\d*", name, re.I)):
        return ""
    return name


def media_fingerprint(key, item):
    # Exclude counters/times: confirmation binds to the observed stream identity.
    evidence = [key, item.get("protocol"), item.get("programs", []), item.get("streams", [])]
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def confirmed_name(key, item):
    confirmation = item.get("confirmation", {})
    if not confirmation.get("invalidated") and confirmation.get("fingerprint") == media_fingerprint(key, item):
        return channel_name(confirmation.get("name"))
    return ""


def recent_checks(values, now, spacing=60, daytime=False, max_age=7 * 86400, daytime_end=18):
    """Only dated, spaced evidence can authorize automatic playlist changes."""
    if not isinstance(values, list):
        return []
    checks = []
    for stamp in sorted({v for v in values if type(v) in (int, float) and 0 <= now - v <= max_age}):
        if daytime and not 9 <= dt.datetime.fromtimestamp(stamp, dt.timezone(dt.timedelta(hours=8))).hour < daytime_end:
            continue
        if not checks or stamp - checks[-1] >= spacing:
            checks.append(stamp)
    return checks


def successful_checks(key, item, now):
    if (item.get("validation") != "media" or item.get("last_check")
            or item.get("success_fingerprint") != media_fingerprint(key, item)):
        return []
    return recent_checks(item.get("success_checks", []), now)


def differences(baseline, observations, catalog=None):
    known = {}
    names = {}
    for row in baseline:
        known.setdefault(row["endpoint"], []).append(row)
        if row.get("name"):
            names.setdefault(row["name"].casefold().strip(), []).append(row)
    result = []
    for key, item in observations.items():
        row = dict(item, endpoint=key)
        original = known.get(key, [])
        media_names = [channel_name(p.get("name")) for p in item.get("programs", [])]
        media_names = [n for n in media_names if n]
        manual = confirmed_name(key, item)
        candidates = sorted({channel_name(r.get("name")) for r in (catalog or {}).get("rows", [])
                             if r.get("endpoint") == key and r.get("protocol") == item.get("protocol")}
                            - {""})
        row.update(name="", name_source="unknown", name_candidates=candidates,
                   catalog_at=(catalog or {}).get("at", 0), fingerprint=media_fingerprint(key, item),
                   verified_count=len(successful_checks(key, item, time.time())),
                   name_confirmed=bool(manual), confirmation_stale=bool(item.get("confirmation") and not manual))
        if original:
            row.update(name=" / ".join(r.get("name", "") for r in original), name_source="playlist")
        elif manual:
            row.update(name=manual, name_source="manual")
        elif len(item.get("programs", [])) == 1 and len(media_names) == 1:
            row.update(name=media_names[0], name_source="stream")
        elif len(candidates) == 1:
            row.update(name=candidates[0], name_source="catalog")
        elif candidates or len(item.get("programs", [])) > 1:
            row["name_source"] = "conflict"
        row["previous"] = []
        if original:
            row["change"] = "existing"
        elif item.get("validation") == "media":
            old = names.get(row["name"].casefold().strip(), []) if row["name"] else []
            row["change"] = "possible_move" if old else "new_media"
            row["previous"] = sorted({r["endpoint"] for r in old})
        else:
            row["change"] = "unverified"
        # An address without packets in a short sample is not an offline channel.
        result.append(row)
    return sorted(result, key=lambda r: (r["change"] == "existing", -r.get("last_seen", 0), r["endpoint"]))
