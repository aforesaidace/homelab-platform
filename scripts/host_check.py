#!/usr/bin/env python3
"""Check that a new (or any) host is fully onboarded: keys, logging, metrics.

The automatable half of the "Add a New Service" SOP in docs/operations.md.
Run it by hand after building a host, or let the Jenkins tofu pipeline run
it on every VM/LXC an apply just created (--from-plan).

    python3 scripts/host_check.py test-vm --ip 10.20.30.67
    python3 scripts/host_check.py transcriber            # IP from inventory
    python3 scripts/host_check.py --from-plan tofu/plan.json --wait-ssh 300
    ... --post http://10.20.30.20:5678/webhook/tofu-plan --commit SHA --url BUILD_URL

Checks, in order (each PASS / WARN / FAIL):
  ping             answers ICMP
  ssh-ansible      `ansible` account + fleet key + passwordless sudo
                   (falls back to root: a fresh LXC before `common` has run)
  ssh-hardening    sshd has password logins off
  dns              <name>.example.lab resolves to the IP on AdGuard
  inventory        listed in ansible/inventory.yml
  node_exporter    :9100/metrics answers
  prometheus       Prometheus scrapes it and `up` is 1 (targets come from
                   Nautobot, synced on a timer: new hosts lag a little)
  alloy            alloy.service is active on the host
  loki             Loki has log lines from it in the last 30 minutes
  patchmon         PatchMon agent enrolled (credentials file present)
  pbs-backup       its VMID is in a Proxmox backup job

Standard library only (runs in the pipeline's python:alpine pod; needs
an `ssh` client). Uses ~/.ssh/ansible_ed25519, never prints secrets.
Exit code 1 if anything FAILs.
"""
import argparse
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOMAIN = "example.lab"
DNS_SERVER = "10.20.1.10"
PROMETHEUS = "http://10.20.30.12:9090"
LOKI = "http://10.20.30.13:3100"
PVE_HOSTS = ["10.20.1.70", "10.20.1.60"]  # pve1, pve2
PROXIES = {"10.20.30.11": "NPM", "10.20.30.46": "Traefik"}  # a web app's name may point here
KEY = os.path.expanduser("~/.ssh/ansible_ed25519")

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
ICON = {PASS: ":white_check_mark:", WARN: ":warning:", FAIL: ":x:"}


def ssh(user, ip, cmd, timeout=20):
    """Run cmd on ip as user with the fleet key; (rc, stdout)."""
    try:
        r = subprocess.run(
            ["ssh", "-i", KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", f"{user}@{ip}", cmd],
            capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 255, str(e)


def http_get(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def dns_a(name, server=DNS_SERVER, timeout=3):
    """Minimal A-record lookup straight against AdGuard (not the local resolver)."""
    qid = random.randint(0, 0xFFFF)
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    q += struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(q, (server, 53))
        data = s.recv(2048)
    finally:
        s.close()
    ancount = struct.unpack(">H", data[6:8])[0]
    i = len(q)
    out = []
    for _ in range(ancount):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i]:
                i += data[i] + 1
            i += 1
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            out.append(socket.inet_ntoa(data[i:i + 4]))
        i += rdlen
    return out


def inventory_ip(name):
    text = (REPO / "ansible/inventory.yml").read_text()
    m = re.search(rf"^\s+{re.escape(name)}(\.{DOMAIN})?:[ \t]*(?:#.*)?\n\s+ansible_host:\s*([\d.]+)", text, re.M)
    return m.group(2) if m else None


def in_inventory(name):
    text = (REPO / "ansible/inventory.yml").read_text()
    return re.search(rf"^\s+{re.escape(name)}(\.{DOMAIN})?:[ \t]*(?:#.*)?$", text, re.M) is not None


def find_vmid(ip):
    """VMID of the guest whose Proxmox config carries this IP (pve1, then pve2)."""
    for pve in PVE_HOSTS:
        rc, out = ssh("root", pve,
                      f"grep -lE 'ip=?{re.escape(ip)}/' /etc/pve/nodes/*/lxc/*.conf "
                      f"/etc/pve/nodes/*/qemu-server/*.conf 2>/dev/null | head -1")
        if rc == 0 and out:
            return pve, Path(out).stem
    return None, None


def check_host(name, ip, wait_ssh=0):
    fqdn = f"{name}.{DOMAIN}"
    res = []

    def add(check, status, note=""):
        res.append((check, status, note))

    # ping
    ok = subprocess.run(["ping", "-c", "1", "-W", "2", ip], capture_output=True).returncode == 0
    add("ping", PASS if ok else FAIL, "" if ok else f"{ip} does not answer")

    # ssh: ansible (fleet policy), else root (fresh LXC)
    deadline = time.time() + wait_ssh
    while True:
        rc, _ = ssh("ansible", ip, "sudo -n true")
        root_rc = 1
        if rc != 0:
            root_rc, _ = ssh("root", ip, "true")
        if rc == 0 or root_rc == 0 or time.time() >= deadline:
            break
        time.sleep(10)
    if rc == 0:
        user = "ansible"
        add("ssh-ansible", PASS, "ansible + fleet key + passwordless sudo")
    elif root_rc == 0:
        user = "root"
        add("ssh-ansible", WARN, "only root works: run the host's playbook (common creates `ansible`)")
    else:
        user = None
        add("ssh-ansible", FAIL, "fleet key rejected for ansible and root")

    sudo = "sudo -n " if user == "ansible" else ""
    if user:
        rc, out = ssh(user, ip, f"{sudo}sshd -T 2>/dev/null | grep -i '^passwordauthentication'")
        add("ssh-hardening", PASS if out.lower().endswith("no") else WARN,
            out or "could not read sshd -T")
    else:
        add("ssh-hardening", FAIL, "no SSH access")

    # dns
    try:
        answers = dns_a(fqdn)
        if ip in answers:
            add("dns", PASS, f"{fqdn} -> {ip}")
        elif set(answers) & set(PROXIES):
            add("dns", PASS, f"{fqdn} -> {PROXIES[answers[0]] if answers[0] in PROXIES else answers} (proxied web app)")
        else:
            add("dns", WARN if not answers else FAIL,
                f"{fqdn} -> {answers or 'no answer'}: add it to ansible/roles/adguard/vars/main.yml")
    except Exception as e:
        add("dns", FAIL, f"lookup failed: {type(e).__name__}")

    add("inventory", PASS if in_inventory(name) else FAIL,
        "" if in_inventory(name) else "not in ansible/inventory.yml")

    # metrics
    try:
        body = http_get(f"http://{ip}:9100/metrics")
        add("node_exporter", PASS if "node_exporter_build_info" in body else WARN, ":9100 answers")
    except Exception:
        add("node_exporter", FAIL, "nothing on :9100: add `- role: node_exporter` to the playbook")

    try:
        q = urllib.parse.quote(f'up{{instance=~"{name}|{ip}:9100|{fqdn}:9100"}}')
        data = json.loads(http_get(f"{PROMETHEUS}/api/v1/query?query={q}"))["data"]["result"]
        if not data:
            add("prometheus", FAIL, "not a scrape target yet (Nautobot sync adds new hosts on a timer)")
        elif any(r["value"][1] == "1" for r in data):
            add("prometheus", PASS, f"scraped by job {data[0]['metric'].get('job')}")
        else:
            add("prometheus", FAIL, "target is down")
    except Exception as e:
        add("prometheus", FAIL, f"query failed: {type(e).__name__}")

    # logging
    if user:
        rc, out = ssh(user, ip, "systemctl is-active alloy 2>/dev/null")
        add("alloy", PASS if out == "active" else FAIL,
            "" if out == "active" else f"alloy is {out or 'missing'}: add `- role: alloy`")
    else:
        add("alloy", FAIL, "no SSH access")

    try:
        start = int((time.time() - 1800) * 1e9)
        q = urllib.parse.quote(f'{{host=~"{fqdn}|{name}"}}')
        data = json.loads(http_get(f"{LOKI}/loki/api/v1/query_range?query={q}&limit=1&start={start}"))
        found = bool(data.get("data", {}).get("result"))
        add("loki", PASS if found else FAIL, "logs arriving" if found else "no logs in the last 30m")
    except Exception as e:
        add("loki", FAIL, f"query failed: {type(e).__name__}")

    # patching
    if user:
        rc, _ = ssh(user, ip, f"{sudo}test -s /etc/patchmon/credentials.yml")
        add("patchmon", PASS if rc == 0 else FAIL,
            "" if rc == 0 else "not enrolled: add `- role: patchmon_agent` + its vault vars")
    else:
        add("patchmon", FAIL, "no SSH access")

    # backups
    pve, vmid = find_vmid(ip)
    if not vmid:
        add("pbs-backup", WARN, "could not map IP to a VMID")
    else:
        rc, out = ssh("root", pve, "pvesh get /cluster/backup --output-format json")
        try:
            jobs = json.loads(out)
            hit = [j.get("id") for j in jobs
                   if j.get("all") or vmid in str(j.get("vmid", "")).split(",")]
            add("pbs-backup", PASS if hit else FAIL,
                f"VMID {vmid} in {', '.join(hit)}" if hit else f"VMID {vmid} is in no backup job")
        except Exception:
            add("pbs-backup", WARN, f"VMID {vmid}: could not read backup jobs")
    return res


def hosts_from_plan(path):
    """(name, ip) for every Proxmox VM/LXC the plan creates."""
    plan = json.load(open(path))
    out = []
    for rc in plan.get("resource_changes", []):
        if "create" not in rc["change"]["actions"]:
            continue
        a = rc["change"].get("after") or {}
        if rc["type"] not in ("proxmox_virtual_environment_vm", "proxmox_virtual_environment_container"):
            continue
        init = (a.get("initialization") or [{}])[0]
        name = a.get("name") or init.get("hostname")
        try:
            ip = init["ip_config"][0]["ipv4"][0]["address"].split("/")[0]
        except (KeyError, IndexError, TypeError):
            ip = None
        if name and ip:
            out.append((name, ip))
    return out


# Checks that only pass after the host's own playbook has run (and, for
# prometheus, after the Nautobot sync picks the host up). Right after tofu
# creates a host these failing is normal, not a fault.
NEEDS_SETUP = {"node_exporter", "alloy", "loki", "patchmon", "prometheus"}


def find_playbook(name):
    """ansible/playbooks/*.yml whose hosts: line names this host, if any."""
    for pb in sorted((REPO / "ansible/playbooks").glob("*.yml")):
        for line in pb.read_text().splitlines():
            if line.strip().startswith("hosts:") and re.search(
                    rf"(^|[\s:,]){re.escape(name)}(\.{DOMAIN})?($|[\s:,])", line.split(":", 1)[1]):
                return pb.relative_to(REPO)
    return None


def render(results, fresh=False):
    lines = []
    for name, ip, res in results:
        fails = [r for r in res if r[1] == FAIL]
        if not fresh:
            lines.append(f"**{name}** ({ip}): " + ("all checks pass" if not fails else f"{len(fails)} check(s) failing"))
            for check, status, note in res:
                lines.append(f"{ICON[status]} `{check}` {note}".rstrip())
            continue
        waiting = [r for r in fails if r[0] in NEEDS_SETUP]
        problems = [r for r in fails if r[0] not in NEEDS_SETUP]
        warns = [r for r in res if r[1] == WARN]
        passed = [r[0] for r in res if r[1] == PASS]
        state = "problems to fix" if problems else "created, waiting for setup" if waiting else "ready"
        lines.append(f"**{name}** ({ip}), just created: {state}")
        if passed:
            lines.append(":white_check_mark: Works: " + ", ".join(f"`{c}`" for c in passed))
        if problems:
            lines.append(":x: **Problems** (not explained by setup still to come):")
            lines += [f"- `{c}` {n}".rstrip() for c, _, n in problems]
        if waiting:
            lines.append(":hourglass: Waiting for setup (normal for a new host): "
                         + ", ".join(f"`{c}`" for c, _, _ in waiting))
        if warns:
            lines.append(":warning: Still to decide:")
            lines += [f"- `{c}` {n}".rstrip() for c, _, n in warns]
        if waiting or problems:
            pb = find_playbook(name)
            run = f"`ansible-playbook -i ansible/inventory.yml {pb} -l {name}.{DOMAIN}`" if pb \
                else f"the host's playbook (none in ansible/playbooks/ names {name} yet)"
            lines.append(f"**Next (devbox):** run {run}, then `python3 scripts/host_check.py {name}`.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="short hostname, e.g. test-vm")
    ap.add_argument("--ip")
    ap.add_argument("--from-plan", help="tofu show -json output: check every VM/LXC it creates")
    ap.add_argument("--wait-ssh", type=int, default=0, help="seconds to wait for SSH (fresh hosts)")
    ap.add_argument("--post", default="", help="n8n webhook URL")
    ap.add_argument("--commit", default="")
    ap.add_argument("--url", default="", help="Jenkins build URL")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.from_plan:
        targets = hosts_from_plan(a.from_plan)
        if not targets:
            print("(plan creates no VMs/LXCs - nothing to check)")
            return 0
    elif a.name:
        ip = a.ip or inventory_ip(a.name)
        if not ip:
            ap.error(f"no --ip given and {a.name} is not in the inventory")
        targets = [(a.name, ip)]
    else:
        ap.error("give a host name or --from-plan")

    results = [(n, ip, check_host(n, ip, a.wait_ssh)) for n, ip in targets]
    text = render(results, fresh=bool(a.from_plan))
    failed = any(s == FAIL for _, _, res in results for _, s, _ in res)
    if a.json:
        print(json.dumps([{"name": n, "ip": ip, "checks": [dict(zip(("check", "status", "note"), r)) for r in res]}
                          for n, ip, res in results], indent=2))
    else:
        print(text)
    if a.post:
        payload = {"event": "result", "kind": "hostcheck", "status": "hostcheck",
                   "title": "New host checks" if a.from_plan else "Host checks", "summary": text + (f"\n{a.url}" if a.url else ""),
                   "commit": a.commit, "build_url": a.url, "failed": failed}
        try:
            req = urllib.request.Request(a.post, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "homelab-host-check"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            print(f"(post to n8n failed: {type(e).__name__}: {e})", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
