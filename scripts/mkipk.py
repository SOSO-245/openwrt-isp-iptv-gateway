#!/usr/bin/env python3
import argparse
import io
import os
import tarfile
import time


def normalize_tarinfo(info):
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 0
    return info


def make_targz(src_dir):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        tf.add(os.path.abspath(src_dir), arcname=".", recursive=True, filter=normalize_tarinfo)
    return buf.getvalue()


def ar_member(name, data):
    if len(name) > 15:
        raise ValueError(f"ar member name too long: {name}")
    header = (
        name.ljust(16)
        + "0".ljust(12)
        + "0".ljust(6)
        + "0".ljust(6)
        + "100644".ljust(8)
        + str(len(data)).ljust(10)
        + "`\n"
    ).encode("ascii")
    out = header + data
    if len(data) % 2:
        out += b"\n"
    return out


def main():
    parser = argparse.ArgumentParser(description="Build a simple OpenWrt .ipk package")
    parser.add_argument("--control", required=True, help="Directory containing control files")
    parser.add_argument("--data", required=True, help="Directory containing package payload")
    parser.add_argument("--output", required=True, help="Output .ipk path")
    args = parser.parse_args()

    control = make_targz(args.control)
    data = make_targz(args.data)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(b"!<arch>\n")
        f.write(ar_member("debian-binary", b"2.0\n"))
        f.write(ar_member("control.tar.gz", control))
        f.write(ar_member("data.tar.gz", data))


if __name__ == "__main__":
    main()
