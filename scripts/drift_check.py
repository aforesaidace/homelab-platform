#!/usr/bin/env python3
"""
drift_check.py — reconcile the docs against live infrastructure.

Built 2026-09-03 after a manual doc-vs-reality audit turned up ~40 stale
entries in a single session: transposed/wrong VMIDs in docs/vms-lxcs.md,
seven live LXCs missing from its tables, guests filed under the wrong node,
seven undocumented NPM proxy hosts in docs/services.md, and one guest
(`traefik`) in no PBS backup job at all.

This is the automatable half of docs/todo.md's "systemic fix for
doc-vs-reality drift" item (d). It does NOT try to judge narrative claims
(whether a `[ ]` todo item is still accurate, whether a Known Issue still
applies) - only the structured, queryable categories where a live source of
truth can be diffed against a doc table.

checks:
  1. Proxmox live guests  vs  docs/vms-lxcs.md "On pve1" / "On pve2" tables
     - VMID present live but not documented (or vice versa)
     - name / node / IP mismatch for a documented VMID
     (this subsumes the old standalone "cross-check documented VMIDs" todo)
  2. NPM proxy hosts       vs  docs/services.md "Reverse Proxy" table
     - live proxy host not in the table (or vice versa)
     - backend IP / port mismatch
  3. PBS backup coverage
     - every live non-template guest is in at least one vzdump job
  4. Grafana dashboards    vs  ansible/roles/grafana/ codified set  (added 2026-09-08)
     - a live dashboard whose uid isn't provisioned from the role (UI-added,
       will vanish if Grafana storage is rebuilt)
     - a codified dashboard missing from live (provisioning failing)
     Skipped (not failed) when no Grafana admin password is available - the
     automated n8n/CI runs don't pass one; local runs on workstation resolve it from
     OpenBao (secret/grafana) via openbao_secrets.py.
  5. "Claimed-live SSO/integration state doesn't match reality" - PLANNED,
     not yet built as real checks (added 2026-09-12). Flagged 2026-09-11 by
     the user after Known Issue #139 (docs said AudioBookRequest's OIDC was
     live; the activating switch was never flipped), then widened 2026-09-12
     into a full doc-claims audit (Known Issue #141's Nautobot verification
     + the Unifi-syslog-never-actually-wired gap in docs/services.md). See
     CHECK5_BACKLOG below - each entry needs its OWN safe-read-path
     investigation before it can become a real check function (ABR's HTTP
     login form caused a real outage, Known Issue #140 - do not assume a
     uniform "just hit the app's API" mechanism works across apps). Today
     this only reports backlog size/items as informational skips, so the
     list stays visible in every run instead of living only in docs/todo.md.

Read-only. Exits 1 if any drift is found, 0 if clean (skipped checks don't fail).

Usage:
    python3 scripts/drift_check.py            # human-readable report
    python3 scripts/drift_check.py --json     # machine-readable (n8n / CI)
"""

import base64
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import yaml

REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()

NPM_URL = "http://10.20.30.11:81"
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://10.20.30.14:3000")

# Phase 3(c), docs/todo.md Initiative 1: reconcile against Nautobot itself,
# not just the markdown docs - drift_check starts collapsing into the SoT.
# Dedicated read-only Nautobot user ("drift-check", view-only permission on
# virtualization.virtualmachine/extras.job/extras.jobresult - see
# scripts/openbao_setup_policies.py's ROLE_PATHS comment), token in OpenBao
# secret/drift-check (own path, not secret/nautobot - that path holds far
# more sensitive things like the superuser token/secret key).
NAUTOBOT_URL = os.environ.get("NAUTOBOT_URL", "https://nautobot.example.lab/api")

# Proxmox: prefer the HTTPS API with a read-only token so this runs anywhere
# (the n8n LXC, a CI runner) without SSH access to the hypervisors. Pass the
# token as the PVE_TOKEN env var, "<tokenid>=<secret>". The scheduled run on
# the n8n LXC uses the existing proxmox2netbox PVEAuditor token (whole-cluster
# read), fetched from OpenBao secret/proxmox by the drift-check-kv AppRole -
# see ansible/roles/drift_check/templates/run.sh.j2; nothing new was minted.
# Falls back to `ssh ansible@<node> sudo pvesh` for local runs on workstation.
PVE_API = os.environ.get("PVE_API_URL", "https://10.20.1.70:8006/api2/json")
_SSH_KEY = os.environ.get(
    "DRIFT_CHECK_SSH_KEY", os.path.expanduser("~/.ssh/ansible_ed25519"))
PVE_SSH = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10",
           "-i", _SSH_KEY, "ansible@10.20.1.70"]

# --- known-intentional exceptions (keep this list short and explained) ---

# Guests deliberately outside PBS coverage.
PBS_EXEMPT = {
    103: "pbs itself - backing up the backup server to itself is pointless",
    202: "truenas - data is on ZFS + the B2 offsite copy, not hypervisor-level vzdump (KI #85)",
    143: "k8s-practice - throwaway teaching infra, explicitly no backup expected",
    111: "winadmin - break-glass Windows 11 installer VM, powered off between uses, "
         "rebuildable from the ISO + a manual Windows setup that's already manual",
}

# Template / build VMs that legitimately have no row in the vms-lxcs.md tables.
DOC_EXEMPT_VMIDS = {8999, 9000, 9001}

# Subdomains that resolve straight to a backend IP via an AdGuard rewrite and
# deliberately never get an NPM proxy host (Kerberos/LDAP, or internal-only
# PKI that must not touch the public ACME/CT path). See docs/services.md's
# "Direct Access (not behind NPM)" section.
NPM_DIRECT_ACCESS = {"step-ca", "prometheus", "loki", "ollama", "freeipa"}

# --------------------------------------------------------------------------
# Check 5 backlog - "claimed-live SSO/integration state doesn't match
# reality" (see module docstring). Built from two passes: a "done"/"live"/
# "verified" claims audit across docs/*.md for 2026-08-12..2026-09-12, and a
# separate "deployed but not fully integrated" sweep of docs/todo.md. Each
# entry is a claim worth re-checking against live state, NOT yet a real
# check function - `status` says how close it is to becoming one.
#
# status values:
#   "scoped"      - safe read path identified, no credential/investigation
#                   gap; next to implement.
#   "needs investigation" - what to query is known in outline, but the
#                   exact safe read path (auth, endpoint, or whether hitting
#                   it live is even safe) hasn't been confirmed yet.
#   "not automatable" - absence-of-a-feature or requires a human
#                   click-through; track in docs/todo.md, not here.
CHECK5_BACKLOG = [
    {
        "claim": "AudioBookRequest login_type is actually 'oidc', not 'forms'",
        "source": "Known Issue #139/#140, docs/todo.md Check 5 v1 (2026-09-11)",
        "safe_read_path": "Postgres `SELECT value FROM config WHERE key = "
                           "'login_type'` on postgres.example.lab, db "
                           "audiobookrequest - do NOT use ABR's own HTTP login "
                           "form (that caused KI #140's outage)",
        "status": "scoped",
    },
    {
        "claim": "nautobot_ssot_unifi has a real SecretsGroup/ExternalIntegration/"
                 "Controller configured, not just the Job self-registered",
        "source": "Known Issue #141 (2026-09-12)",
        "safe_read_path": "Nautobot REST API GET /api/extras/secrets-groups/ + "
                           "/api/extras/external-integrations/ with the existing "
                           "superuser API token - cheap once real Unifi creds land",
        "status": "resolved",
        "resolution": "CONFIRMED TRUE 2026-09-12 - built and verified live this "
                       "session, not just checked: Secret/SecretsGroup/"
                       "ExternalIntegration/Controller objects all real, a real "
                       "sync ran (5 devices/42 interfaces/2 IPs). See docs/todo.md "
                       "Initiative 1 Phase 2.",
    },
    {
        "claim": "PatchMon OIDC role-mapping (admin_group/user_group) is set, "
                 "not still null",
        "source": "docs/todo.md SSO rollout, PatchMon entry (2026-09-01)",
        "safe_read_path": "internal GET /api/v1/auth/oidc/settings - same route "
                           "used to build the integration originally",
        "status": "needs investigation",
    },
    {
        "claim": "homelab-users/homelab-readonly FreeIPA groups are still empty "
                 "- every app's RBAC below admin has never been exercised",
        "source": "docs/todo.md SSO rollout, Grafana/NetBox/Nextcloud/Gitea/"
                   "Paperless-ngx entries (2026-08-25/26)",
        "safe_read_path": "read-only ldapsearch against FreeIPA for group "
                           "member count - existing bind account, no new cred",
        "status": "scoped",
    },
    {
        "claim": "Plex's Unifi WAN port-forward still targets the current "
                 "arr_media IP, hasn't silently drifted again",
        "source": "Known Issue #124 (2026-08-25)",
        "safe_read_path": "Unifi controller API port-forward read - reuse "
                           "whatever credential lands from the in-progress "
                           "nautobot_ssot_unifi Unifi API setup",
        "status": "needs investigation",
    },
    {
        "claim": "authentik_setup.py's captured Gitea OAuth2 secret still "
                 "matches what's live in Gitea's app.ini, hasn't re-drifted",
        "source": "Known Issue #132 (2026-09-02)",
        "safe_read_path": "SSH-read /etc/gitea/app.ini's oauth2 section vs the "
                           "Authentik provider object over its API",
        "status": "needs investigation",
    },
    {
        "claim": "The stale second Ansible checkout that crash-looped OpenBao "
                 "is actually gone, not just fixed once",
        "source": "Known Issue #101 (2026-08-19)",
        "safe_read_path": "SSH `find` for a second homelab checkout path on "
                           "the openbao LXC - no new credential needed",
        "status": "scoped",
    },
    {
        "claim": "NPM's /data mount still points at the image's declared "
                 "/config volume (the symlink fix holds across recreates)",
        "source": "Known Issue #111 (2026-08-23)",
        "safe_read_path": "`docker inspect npm --format '{{json .Mounts}}'` "
                           "over SSH - cheap, no new credential",
        "status": "scoped",
    },
    {
        "claim": "AMP <-> Unifi integration ('Unifi side still needed') - "
                 "current status unconfirmed, not re-read this session",
        "source": "docs/todo.md, older item (pre-2026-08-12, surfaced during "
                   "the 2026-09-12 audit sweep)",
        "safe_read_path": "re-read the surrounding docs/todo.md section first "
                           "- unknown whether this is even still open",
        "status": "needs investigation",
    },
    {
        "claim": "Six services that shared one admin password (KI #121) still "
                 "have no idempotent reset mechanism",
        "source": "Known Issue #121 (2026-08-25)",
        "safe_read_path": None,
        "status": "not automatable",
    },
]


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}...: rc={r.returncode} {r.stderr.strip()[:200]}")
    return r.stdout


def load_secrets():
    # Local fallback when the env vars below aren't set: the old secrets.yml
    # key names, resolved in OpenBao via the read-only role (workstation only - on
    # the n8n LXC / in CI the .vault_secret_ids.yml file is absent and every
    # .get() returns its default, so env vars are used as before).
    from openbao_secrets import load_secrets as _load
    return _load(prefer={"nautobot_api_token": "drift-check"})


_SECRETS = load_secrets()


def _pve_token():
    tok = os.environ.get("PVE_TOKEN")
    if tok:
        return tok
    tid = _SECRETS.get("proxmox2netbox_token_id")
    sec = _SECRETS.get("proxmox2netbox_token_secret")
    if tid and sec:
        return f"{tid}={sec}"
    return None


def _nautobot_api(path):
    """GET path (e.g. '/virtualization/virtual-machines/?limit=1') against
    Nautobot's REST API using the read-only drift-check token."""
    token = os.environ.get("NAUTOBOT_API_TOKEN") or _SECRETS.get("nautobot_api_token")
    if not token:
        raise RuntimeError(
            "Nautobot API token not found (NAUTOBOT_API_TOKEN env, or "
            "secret/drift-check's nautobot_api_token via the local OpenBao "
            "fallback on workstation)"
        )
    req = urllib.request.Request(f"{NAUTOBOT_URL}{path}", headers={"Authorization": f"Token {token}"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def pvesh(path):
    """`path` is a pvesh-style path, e.g. '/cluster/resources --type vm'.
    Uses the Proxmox API + token if available, else falls back to SSH+pvesh."""
    token = _pve_token()
    if token:
        p, _, rest = path.strip().lstrip("/").partition(" ")
        qs = ""
        m = re.search(r"--(\S+)\s+(\S+)", rest)
        if m:
            qs = "?" + urllib.parse.urlencode({m.group(1): m.group(2)})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # internal PVE self-signed / step-ca
        req = urllib.request.Request(f"{PVE_API}/{p}{qs}",
                                    headers={"Authorization": f"PVEAPIToken={token}"})
        return json.loads(urllib.request.urlopen(req, context=ctx, timeout=20).read())["data"]
    return json.loads(sh(PVE_SSH + [f"sudo -n pvesh get {path} --output-format json 2>/dev/null"]))


# --------------------------------------------------------------------------
# live sources of truth
# --------------------------------------------------------------------------

def live_guests():
    """vmid -> {name, node, kind ('LXC'/'VM'), template, status}"""
    out = {}
    for r in pvesh("/cluster/resources --type vm"):
        out[int(r["vmid"])] = {
            "name": r.get("name", ""),
            "node": r.get("node", ""),
            "kind": "LXC" if r.get("type") == "lxc" else "VM",
            "template": bool(r.get("template")),
            "status": r.get("status", ""),
        }
    return out


def live_guest_ips(guests):
    """{vmid: ip} for every guest whose config declares a static IP.

    API mode: one /config call per guest (HTTP, fast). SSH mode: one
    `grep` over /etc/pve. LXCs put the IP in `net0` (ip=...), cloud-init
    VMs in `ipconfig0`; DHCP / agent-only guests won't appear and the
    caller skips the IP comparison for them."""
    ips = {}
    if _pve_token():
        for vmid, g in guests.items():
            if g["template"]:
                continue
            endpoint = "lxc" if g["kind"] == "LXC" else "qemu"
            try:
                cfg = pvesh(f"/nodes/{g['node']}/{endpoint}/{vmid}/config")
            except Exception:
                continue
            for key in ("net0", "ipconfig0"):
                m = re.search(r"(?:^|[,\s])ip=(\d+\.\d+\.\d+\.\d+)", cfg.get(key, ""))
                if m:
                    ips[vmid] = m.group(1)
                    break
        return ips
    cmd = ('grep -H "^net0:\\|^ipconfig0:" '
           '/etc/pve/nodes/*/lxc/*.conf /etc/pve/nodes/*/qemu-server/*.conf 2>/dev/null')
    out = sh(PVE_SSH + [f"sudo -n bash -c '{cmd}'"])
    for line in out.splitlines():
        pm = re.search(r"/(\d{2,4})\.conf:", line)
        im = re.search(r"(?:^|[,\s])ip=(\d+\.\d+\.\d+\.\d+)", line)
        if pm and im:
            ips[int(pm.group(1))] = im.group(1)
    return ips


def npm_proxy_hosts():
    email = os.environ.get("NPM_EMAIL") or _SECRETS.get("homepage_npm_email")
    password = os.environ.get("NPM_PASSWORD") or _SECRETS.get("homepage_npm_password")
    if not (email and password):
        raise RuntimeError("NPM credentials not found (OpenBao secret/homepage via the local fallback, or NPM_EMAIL/NPM_PASSWORD env)")
    payload = json.dumps({"identity": email, "secret": password}).encode()
    req = urllib.request.Request(f"{NPM_URL}/api/tokens", data=payload,
                                headers={"Content-Type": "application/json"}, method="POST")
    token = json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]
    req = urllib.request.Request(f"{NPM_URL}/api/nginx/proxy-hosts",
                                headers={"Authorization": f"Bearer {token}"})
    hosts = json.loads(urllib.request.urlopen(req, timeout=15).read())
    out = {}
    for h in hosts:
        for dom in h.get("domain_names", []):
            out[dom] = {
                "ip": h.get("forward_host", ""),
                "port": int(h.get("forward_port", 0)),
                "enabled": bool(h.get("enabled")),
            }
    return out


def pbs_covered_vmids():
    covered = set()
    for job in pvesh("/cluster/backup"):
        for v in str(job.get("vmid", "")).split(","):
            v = v.strip()
            if v.isdigit():
                covered.add(int(v))
    return covered


def _grafana_password():
    return os.environ.get("GRAFANA_PASSWORD") or _SECRETS.get("grafana_admin_password")


def grafana_live_dashboards():
    """{uid: {'title': str, 'folder': str}} for every live dashboard.

    The local admin account was renamed 'admin' -> 'ops' during the OIDC
    pilot; a freshly-provisioned Grafana would still be 'admin'. Try the
    override, then both."""
    pw = _grafana_password()
    if not pw:
        raise RuntimeError("no Grafana admin password (OpenBao secret/grafana via the local fallback / GRAFANA_PASSWORD)")
    users = []
    for u in (os.environ.get("GRAFANA_USER"), "ops", "admin"):
        if u and u not in users:
            users.append(u)
    last = None
    for user in users:
        auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
        req = urllib.request.Request(
            f"{GRAFANA_URL}/api/search?type=dash-db",
            headers={"Authorization": f"Basic {auth}"})
        try:
            data = json.loads(urllib.request.urlopen(req, timeout=15).read())
            return {d["uid"]: {"title": d.get("title", ""),
                               "folder": d.get("folderTitle", "General")}
                    for d in data}
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 401:
                raise
    raise RuntimeError(f"Grafana auth failed for user(s) {users} (last: {last})")


def grafana_expected_dashboards():
    """({uid: source}, [unresolved]) from ansible/roles/grafana/.

    Custom dashboards ship their uid in the repo. grafana.com dashboard IDs
    (grafana_dashboard_ids) don't - their uid is whatever the downloaded
    revision carries, so resolve it from grafana.com the same way the
    Ansible role's download step does. A grafana.com failure is 'unresolved'
    (can't verify), not drift."""
    with open(f"{REPO_ROOT}/ansible/roles/grafana/defaults/main.yml") as f:
        defaults = yaml.safe_load(f) or {}
    expected, unresolved = {}, []
    for fname in defaults.get("grafana_custom_dashboards", []):
        try:
            with open(f"{REPO_ROOT}/ansible/roles/grafana/files/{fname}") as f:
                expected[json.load(f)["uid"]] = f"custom:{fname}"
        except (OSError, KeyError, json.JSONDecodeError) as e:
            unresolved.append((fname, f"{type(e).__name__}: {e}"))
    for gid in defaults.get("grafana_dashboard_ids", []):
        try:
            url = f"https://grafana.com/api/dashboards/{gid}/revisions/latest/download"
            dash = json.loads(urllib.request.urlopen(url, timeout=20).read())
            expected[dash["uid"]] = f"grafana.com:{gid}"
        except Exception as e:  # network / parse / missing uid
            unresolved.append((f"grafana.com:{gid}", f"{type(e).__name__}: {e}"))
    return expected, unresolved


# --------------------------------------------------------------------------
# doc parsing
# --------------------------------------------------------------------------

# | ID | Name | Type | IP | ...  - the IP column isn't always a real IP
# (e.g. winadmin's "DHCP (Default VLAN)"), so capture it loosely and only
# compare it when it actually looks like an address.
_ROW = re.compile(r"^\|\s*(\d{2,4})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def parse_vmslxcs_tables():
    """(vmid, name, node, ip) for every row under 'On pve1' / 'On pve2'."""
    rows = []
    section = None
    with open(f"{REPO_ROOT}/docs/vms-lxcs.md") as f:
        for line in f:
            if line.startswith("### On pve1"):
                section = "pve1"
            elif line.startswith("### On pve2"):
                section = "pve2"
            elif line.startswith("### ") and section:
                section = None
            m = _ROW.match(line)
            if m and section:
                rows.append((int(m.group(1)), m.group(2).strip(),
                             section, m.group(4).strip()))
    return rows


_SVC_ROW = re.compile(
    r"^\|\s*([a-z0-9.-]+\.example\.site)\s*\|\s*([0-9.]+)\s*\|\s*(\d+)\s*\|")


def parse_services_npm_table():
    """{subdomain: (backend_ip, port)} from the 'Reverse Proxy' table."""
    out = {}
    in_section = False
    with open(f"{REPO_ROOT}/docs/services.md") as f:
        for line in f:
            if line.startswith("### Reverse Proxy"):
                in_section = True
                continue
            if in_section and line.startswith("### "):
                break
            m = _SVC_ROW.match(line)
            if m and in_section:
                out[m.group(1)] = (m.group(2), int(m.group(3)))
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_proxmox_vs_doc(findings):
    live = live_guests()
    ips = live_guest_ips(live)
    doc = {vmid: (name, node, ip) for vmid, name, node, ip in parse_vmslxcs_tables()}

    real = {v: g for v, g in live.items()
            if not g["template"] and v not in DOC_EXEMPT_VMIDS}

    for vmid, g in sorted(real.items()):
        if vmid not in doc:
            findings.append(("proxmox-vs-vmslxcs", "drift",
                             f"VMID {vmid} ({g['name']}, {g['kind']} on {g['node']}) "
                             f"is live but has no row in docs/vms-lxcs.md"))
            continue
        d_name, d_node, d_ip = doc[vmid]
        if d_name.lower() != g["name"].lower():
            findings.append(("proxmox-vs-vmslxcs", "drift",
                             f"VMID {vmid}: doc name '{d_name}', live name '{g['name']}'"))
        if d_node != g["node"]:
            findings.append(("proxmox-vs-vmslxcs", "drift",
                             f"VMID {vmid} ({g['name']}): doc says node '{d_node}', "
                             f"live on '{g['node']}'"))
        live_ip = ips.get(vmid)
        if live_ip and _IP_RE.match(d_ip) and live_ip != d_ip:
            findings.append(("proxmox-vs-vmslxcs", "drift",
                             f"VMID {vmid} ({g['name']}): doc IP {d_ip}, live IP {live_ip}"))

    for vmid, (name, node, _ip) in sorted(doc.items()):
        if vmid not in live:
            findings.append(("proxmox-vs-vmslxcs", "drift",
                             f"VMID {vmid} ('{name}', {node} table) is in docs/vms-lxcs.md "
                             f"but no such guest exists"))


def check_nautobot_vs_proxmox(findings):
    """Reconcile Nautobot's Proxmox-synced VirtualMachines against live
    Proxmox - Phase 3(c), docs/todo.md Initiative 1. Also guards against
    the exact "stalled sync = silent coverage loss" risk KI #117 already
    hit once for the old proxmox2netbox schedule (docs/todo.md's Phase 3
    note under the Prometheus NetBox http_sd item) - now pointed at the
    Nautobot Job that replaced it instead.
    """
    live = live_guests()
    real = {v: g for v, g in live.items()
            if not g["template"] and v not in DOC_EXEMPT_VMIDS}
    live_names = {g["name"] for g in real.values()}

    vms = _nautobot_api("/virtualization/virtual-machines/?cluster=example&limit=200")
    nautobot_names = {vm["name"] for vm in vms.get("results", [])}

    for name in sorted(live_names - nautobot_names):
        findings.append(("nautobot-vs-proxmox", "drift",
                         f"{name} is a live Proxmox guest but has no matching "
                         f"VirtualMachine in Nautobot (stale/never-run sync?)"))
    for name in sorted(nautobot_names - live_names):
        findings.append(("nautobot-vs-proxmox", "drift",
                         f"Nautobot has VirtualMachine {name} but no matching "
                         f"live Proxmox guest (deleted guest never pruned - "
                         f"this Job doesn't delete stale VMs, lean v1 scope)"))

    jobs = _nautobot_api("/extras/jobs/?name__ic=Proxmox%20to%20Nautobot")
    job = next((j for j in jobs.get("results", []) if j["name"] == "Proxmox to Nautobot"), None)
    if not job:
        findings.append(("nautobot-vs-proxmox", "drift",
                         "The 'Proxmox to Nautobot' Job is not installed in Nautobot at all"))
        return

    results = _nautobot_api(f"/extras/job-results/?job_model={job['id']}&status=SUCCESS&limit=1")
    latest = results.get("results", [])
    if not latest:
        findings.append(("nautobot-vs-proxmox", "drift",
                         "The 'Proxmox to Nautobot' Job has never completed successfully"))
        return

    done = latest[0].get("date_done")
    if done:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(done.replace("Z", "+00:00"))
        if age > timedelta(hours=26):
            findings.append(("nautobot-vs-proxmox", "drift",
                             f"The 'Proxmox to Nautobot' Job's last success was "
                             f"{age} ago (>26h) - no recurring schedule exists for "
                             f"it yet, this only flags staleness once it happens"))


def check_npm_vs_doc(findings):
    live = npm_proxy_hosts()
    doc = parse_services_npm_table()

    doc_domains = set(doc)
    for dom, info in sorted(live.items()):
        if dom == "npm.example.lab":
            continue  # NPM's own UI, served by NPM itself, not a normal proxy row
        if dom not in doc_domains:
            findings.append(("npm-vs-services", "drift",
                             f"NPM proxies {dom} -> {info['ip']}:{info['port']} "
                             f"but it's not in the docs/services.md Reverse Proxy table"))
            continue
        d_ip, d_port = doc[dom]
        if (d_ip, d_port) != (info["ip"], info["port"]):
            findings.append(("npm-vs-services", "drift",
                             f"{dom}: doc says {d_ip}:{d_port}, NPM forwards to "
                             f"{info['ip']}:{info['port']}"))

    for dom in sorted(doc_domains - set(live)):
        short = dom.split(".")[0]
        if short in NPM_DIRECT_ACCESS:
            continue
        findings.append(("npm-vs-services", "drift",
                         f"{dom} is in the docs/services.md Reverse Proxy table "
                         f"but has no live NPM proxy host"))


def check_pbs_coverage(findings):
    live = live_guests()
    covered = pbs_covered_vmids()
    for vmid, g in sorted(live.items()):
        if g["template"] or vmid in PBS_EXEMPT or vmid in covered:
            continue
        findings.append(("pbs-coverage", "drift",
                         f"VMID {vmid} ({g['name']}, on {g['node']}) is in no PBS vzdump job"))


def check_grafana_dashboards(findings):
    if not _grafana_password():
        findings.append(("grafana-dashboards", "skip",
                         "no Grafana admin password available (OpenBao secret/grafana / "
                         "GRAFANA_PASSWORD) - skipped"))
        return
    live = grafana_live_dashboards()
    expected, unresolved = grafana_expected_dashboards()

    for src, err in unresolved:
        findings.append(("grafana-dashboards", "skip",
                         f"couldn't resolve codified dashboard {src} to a uid "
                         f"({err}) - can't verify it's provisioned"))

    for uid, meta in sorted(live.items(), key=lambda kv: kv[1]["title"].lower()):
        if uid not in expected:
            findings.append(("grafana-dashboards", "drift",
                             f"Grafana has dashboard '{meta['title']}' (uid {uid}, "
                             f"folder {meta['folder']}) that is NOT provisioned from "
                             f"ansible/roles/grafana/ - UI-added, will vanish on a "
                             f"storage rebuild"))

    for uid, src in sorted(expected.items()):
        if uid not in live:
            findings.append(("grafana-dashboards", "drift",
                             f"dashboard {src} (uid {uid}) is codified but not live in "
                             f"Grafana - provisioning may be failing"))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def check5_backlog(findings):
    """Not a live check - CHECK5_BACKLOG has no automated verification yet.
    Reports as skips so the list stays visible in every run's output/JSON
    instead of only living in docs/todo.md. See CHECK5_BACKLOG's docstring
    for what 'scoped' vs 'needs investigation' vs 'not automatable' mean.
    'resolved' items are excluded from the live report (their claim is
    confirmed true, nothing left to track) but kept in the list itself as
    a record of what this backlog already caught."""
    by_status = {}
    for item in CHECK5_BACKLOG:
        if item["status"] == "resolved":
            continue
        by_status.setdefault(item["status"], []).append(item["claim"])
    for status, claims in sorted(by_status.items()):
        findings.append(("check5-backlog", "skip",
                         f"{len(claims)} claim(s) marked '{status}', not yet "
                         f"an automated check: " + "; ".join(claims)))


CHECKS = [
    ("proxmox-vs-vmslxcs", check_proxmox_vs_doc),
    ("nautobot-vs-proxmox", check_nautobot_vs_proxmox),
    ("npm-vs-services", check_npm_vs_doc),
    ("pbs-coverage", check_pbs_coverage),
    ("grafana-dashboards", check_grafana_dashboards),
    ("check5-backlog", check5_backlog),
]


def main():
    as_json = "--json" in sys.argv
    findings = []
    errors = []

    for name, fn in CHECKS:
        try:
            fn(findings)
        except Exception as exc:  # a check failing shouldn't kill the others
            errors.append((name, f"{type(exc).__name__}: {exc}"))

    skips = [(c, d) for c, sv, d in findings if sv == "skip"]
    findings = [(c, sv, d) for c, sv, d in findings if sv != "skip"]
    drift = bool(findings)

    if as_json:
        by_check = {}
        for cat, sev, detail in findings:
            by_check.setdefault(cat, []).append({"severity": sev, "detail": detail})
        print(json.dumps({
            "drift_found": drift,
            "check_errors": [{"check": c, "error": e} for c, e in errors],
            "skipped": [{"check": c, "reason": r} for c, r in skips],
            "findings": by_check,
        }, indent=2))
    else:
        print("=== drift_check ===\n")
        for cat, _fn in CHECKS:
            hits = [d for c, _sv, d in findings if c == cat]
            err = next((e for c, e in errors if c == cat), None)
            skipped = [r for c, r in skips if c == cat]
            if err:
                print(f"[ERROR]  {cat}: {err}")
            elif hits:
                print(f"[DRIFT]  {cat}  ({len(hits)})")
                for d in hits:
                    print(f"    - {d}")
            elif skipped:
                print(f"[SKIP]   {cat}: {skipped[0]}")
            else:
                print(f"[OK]     {cat}")
        print(f"\n{len(findings)} drift item(s)"
              + (f", {len(errors)} check error(s)" if errors else "")
              + (f", {len(skips)} skipped" if skips else ""))

    sys.exit(1 if drift or errors else 0)


if __name__ == "__main__":
    main()
