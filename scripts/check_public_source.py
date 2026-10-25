#!/usr/bin/env python3
"""Check the selected public source delta without printing matched secret values.

This is a publication guard, not a replacement for human review or a full
credential scanner. Existing unchanged binary releases/history are out of scope.
"""
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = tuple(ipaddress.ip_network(value) for value in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
RULES = {
    'private-key': rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    'github-token': rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
    'cloud-key': rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'personal-path': rb'/(?:Users|home)/[A-Za-z][A-Za-z0-9_.-]+/',
    'credential-url': rb'https?://[^\s/"\x27:]+:[^\s/@"\x27]+@',
    'credential-literal': rb'(?i)\b(?:password|passwd|api[_-]?key|api[_-]?token|secret)\s*[:=]\s*["\x27][A-Za-z0-9_!@#$%^&*().+=/-]{8,}["\x27]',
}


def candidates():
    tracked = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACMR', '-z', 'HEAD'], cwd=ROOT)
    added = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    return sorted(set(part.decode() for part in (tracked + added).split(b'\0') if part))


def inspect(name, data):
    path = Path(name)
    findings = []
    if (path.name in ('.env', 'api-token', 'state.json', 'config.json')
            or path.suffix in ('.pcap', '.log', '.pem', '.key')
            or any(part.startswith('backup-') for part in path.parts)):
        findings.append('runtime-or-secret-file')
    if path.suffix in ('.png', '.jpg', '.jpeg', '.webp', '.ipk', '.gz', '.zip'):
        findings.append('binary-needs-manual-review')
        return findings
    for label, pattern in RULES.items():
        if re.search(pattern, data):
            findings.append(label)
    # Test fixtures use TEST-NET addresses, never a household private subnet.
    # Only the older documented installation examples are allowed here.
    legacy_example = name.startswith(('openwrt/', 'docker/'))
    for value in re.findall(rb'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', data):
        try:
            ip = ipaddress.ip_address(value.decode())
        except ValueError:
            continue
        if not any(ip in network for network in PRIVATE):
            continue
        if legacy_example and str(ip) in ('192.168.1.1', '192.168.1.100'):
            continue
        # These three networks declare the policy, not a deployed address.
        if name == 'scripts/check_public_source.py' and str(ip) in ('10.0.0.0', '172.16.0.0', '192.168.0.0', '192.168.1.1', '192.168.1.100'):
            continue
        findings.append('private-address')
        break
    return findings


def main():
    names = candidates()
    findings = []
    for name in names:
        path = ROOT / name
        if path.is_symlink():
            findings.append((name, ['symlink-needs-review']))
        elif path.is_file():
            result = inspect(name, path.read_bytes())
            if result:
                findings.append((name, result))
    for name, reasons in findings:
        print(f'{name}: {", ".join(reasons)}')
    print(f'Checked {len(names)} changed/new files; flagged {len(findings)} files. No matched values printed.')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
