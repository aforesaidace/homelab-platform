#!/usr/bin/env python3
"""
nautobot_prometheus_sd.py — writes the Prometheus `node` job's file_sd
targets from Nautobot, the source of truth (docs/todo.md Initiative 1).

Since 2026-10-24 this is the ONLY source of node_exporter targets: the
inventory-loop `node` job and group_vars' hand-kept target list, role map
and exclude list are gone. What gets scraped on :9100:
  - every VirtualMachine in the example cluster (Proxmox SSoT job)
  - every Device tagged `manual-seed` (scripts/nautobot_seed/seed.yml)
with an IP, minus anything whose `node_exporter` custom field is false
(unset = the field's default, true). Labels: `instance` = Nautobot name,
`role` = the Nautobot role, slugified.

Device IP: primary_ip4 if set, else the seeded IP whose dns_name is
<name>.example.lab - seeded IPs aren't bound to interfaces until the
UniFi job's queryset is tag-scoped (seed phase 2), so devices have no
primary IP yet.

Safety: refuses to write (exit 3, old file kept) if the target count drops
below SD_MIN_RATIO of the current file's, so a Nautobot outage or a bad
filter can't silently unmonitor the fleet. Writes its last-success time to
the node_exporter textfile collector; NautobotSdStale fires when that stops
moving (refused, crashed or not run).

Deliberately file_sd, not the third-party nautobot-plugin-prometheus-sd (no
PyPI release in 12+ months, needs an image rebuild); Prometheus watches the
file and reloads on change.

Usage:
    NAUTOBOT_API_TOKEN=... python3 scripts/nautobot_prometheus_sd.py
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.request

NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "https://nautobot.example.lab/api")
OUTPUT_PATH = os.environ.get("SD_OUTPUT_PATH", "/etc/prometheus/file_sd/nautobot_vms.json")
TEXTFILE_DIR = os.environ.get("SD_TEXTFILE_DIR", "/var/lib/node_exporter/textfile")
NODE_PORT = int(os.environ.get("PROMETHEUS_NODE_PORT", "9100"))
MIN_RATIO = float(os.environ.get("SD_MIN_RATIO", "0.8"))
DOMAIN = "example.lab"
SEED_TAG = "manual-seed"


def get(token, path):
    req = urllib.request.Request(
        f"{NAUTOBOT_URL}/{path}",
        headers={"Authorization": f"Token {token}", "Accept": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()).read())["results"]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "unassigned").lower()).strip("-")


def ip_of(obj):
    ip = (obj.get("primary_ip4") or {}).get("display") or ""
    return ip.split("/")[0] if "/" in ip else None


def write_metrics(**values):
    """Best-effort: the alerts read these; a missing dir must not block the sync."""
    if not os.path.isdir(TEXTFILE_DIR):
        return
    path = os.path.join(TEXTFILE_DIR, "nautobot_sd.prom")
    with open(path + ".tmp", "w") as f:
        for k, v in values.items():
            f.write(f"# TYPE nautobot_sd_{k} gauge\nnautobot_sd_{k} {v}\n")
    os.replace(path + ".tmp", path)


def build_targets(token):
    vms = get(token, "virtualization/virtual-machines/?cluster=example&limit=500&depth=1")
    devices = get(token, f"dcim/devices/?tags={SEED_TAG}&status=Active&limit=500&depth=1")
    seeded_ips = {ip["dns_name"]: ip["address"].split("/")[0]
                  for ip in get(token, f"ipam/ip-addresses/?tags={SEED_TAG}&limit=500") if ip.get("dns_name")}

    targets, skipped = [], []
    for obj, kind in [(v, "vm") for v in vms] + [(d, "device") for d in devices]:
        name = obj["name"]
        if (obj.get("custom_fields") or {}).get("node_exporter") is False:
            continue
        ip = ip_of(obj) or (seeded_ips.get(f"{name}.{DOMAIN}") if kind == "device" else None)
        if not ip:
            skipped.append(name)
            continue
        targets.append({
            "targets": [f"{ip}:{NODE_PORT}"],
            "labels": {"instance": name, "ip": ip, "role": slug((obj.get("role") or {}).get("name"))},
        })
    targets.sort(key=lambda t: t["labels"]["instance"])
    return targets, skipped


def main():
    token = os.environ.get("NAUTOBOT_API_TOKEN")
    if not token:
        print("NAUTOBOT_API_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    targets, skipped = build_targets(token)

    previous = 0
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            previous = len(json.load(f))
    if previous and len(targets) < previous * MIN_RATIO:
        print(f"refusing: {len(targets)} targets vs {previous} in the current file "
              f"(< {MIN_RATIO:.0%}); old file kept", file=sys.stderr)
        sys.exit(3)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH + ".tmp", "w") as f:
        json.dump(targets, f, indent=2)
    os.replace(OUTPUT_PATH + ".tmp", OUTPUT_PATH)  # atomic - Prometheus never sees a half-written file
    write_metrics(last_success_timestamp_seconds=int(time.time()), targets=len(targets))

    print(f"wrote {len(targets)} targets to {OUTPUT_PATH}"
          + (f" (skipped, no IP: {', '.join(skipped)})" if skipped else ""))


if __name__ == "__main__":
    main()
