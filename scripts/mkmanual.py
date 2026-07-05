#!/usr/bin/env python3
import argparse
import io
import os
import tarfile
import time


INSTALL_SH = """#!/bin/sh
set -eu
BASE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
tar -C / -xzf "$BASE_DIR/rootfs.tar.gz"
chmod 755 /www/cgi-bin/iptv-action /www/cgi-bin/iptv-status /usr/bin/soso-epg-gen /etc/init.d/iptv-tc-bridge 2>/dev/null || true
chmod 755 /etc/hotplug.d/iface/95-iptv-udpxy /etc/hotplug.d/iface/96-iptv-capture-repair /etc/hotplug.d/net/96-iptv-capture-repair 2>/dev/null || true
rm -f /tmp/luci-indexcache.* 2>/dev/null || true
rm -rf /tmp/luci-modulecache 2>/dev/null || true
mkdir -p /var/run/ubus 2>/dev/null || true
if [ -S /var/run/ubus/ubus.sock ] && [ ! -e /var/run/ubus.sock ]; then
  ln -s /var/run/ubus/ubus.sock /var/run/ubus.sock 2>/dev/null || true
elif [ -S /var/run/ubus.sock ] && [ ! -e /var/run/ubus/ubus.sock ]; then
  ln -s ../ubus.sock /var/run/ubus/ubus.sock 2>/dev/null || true
fi
if ! ubus list >/dev/null 2>&1; then
  if [ -x /etc/init.d/ubus ]; then
    /etc/init.d/ubus restart >/dev/null 2>&1 || true
    sleep 1
  fi
fi
if ! ubus list >/dev/null 2>&1; then
  echo "WARNING: ubus is still unavailable; keep system auth backend untouched and reboot if login is unavailable."
fi
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
for svc in higoros higoos higoros-api higoos-api higo-api; do
  [ -x "/etc/init.d/$svc" ] && "/etc/init.d/$svc" restart >/dev/null 2>&1 || true
done
/etc/init.d/uhttpd reload >/dev/null 2>&1 || /etc/init.d/uhttpd restart >/dev/null 2>&1 || true
echo "SOSO IPTV Gateway manual install complete."
echo "Open: http://ROUTER-IP:8080/iptv/"
"""


def reset_info(info):
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    return info


def make_rootfs(data_dir):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        tf.add(os.path.abspath(data_dir), arcname=".", recursive=True, filter=reset_info)
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description="Build manual installer tarball")
    parser.add_argument("--data", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    rootfs = make_rootfs(args.data)
    with tarfile.open(args.output, "w:gz", format=tarfile.GNU_FORMAT) as tf:
        install_bytes = INSTALL_SH.encode("utf-8")
        info = tarfile.TarInfo("install.sh")
        info.size = len(install_bytes)
        info.mode = 0o755
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        tf.addfile(info, io.BytesIO(install_bytes))

        info = tarfile.TarInfo("rootfs.tar.gz")
        info.size = len(rootfs)
        info.mode = 0o644
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        tf.addfile(info, io.BytesIO(rootfs))


if __name__ == "__main__":
    main()
