#!/usr/bin/env python3
"""
check_stale_ip_refs.py — Check every external system that can reference a
service by raw IP for a stale rule pointing at a retired address.

Built 2026-08-25 after a real incident (Known Issue #124): Plex's Unifi WAN
port-forward rule kept pointing at the old `arr` LXC's IP for four days
after the arr split moved Plex to a new host, because nothing checked for
stale references outside this repo's own IaC. IPs in this fleet are already
static (see tofu/modules/proxmox_lxc's ip_config block) - the real gap is
that Unifi port-forwards, AdGuard DNS rewrites, and NPM proxy hosts all
target a raw IP as their source of truth, and none of them update
automatically when a service moves to a new LXC/VMID.

Run this any time a service's IP is retired (an LXC split, a VMID rebuild
that lands on a new address, a manual re-IP) - point it at the OLD IP(s)
and treat any hit as a real, unresolved migration step, not a false
positive. See docs/operations.md's "Retiring or Changing a Service's IP"
checklist for when this fits into the larger process.

Usage:
    python3 scripts/check_stale_ip_refs.py 10.20.40.20 [10.20.40.21 ...]
"""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import ssl


REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()

UNIFI_URL = "https://10.20.1.1"
ADGUARD_URL = "http://adguard.example.lab"
NPM_URL = "http://10.20.30.11:81"


def load_secrets():
    # secrets.yml is retired - same key names, resolved in OpenBao.
    from openbao_secrets import load_secrets as _load
    return _load()


def vault_get(root_token, path):
    req = urllib.request.Request(
        f"http://10.20.30.22:8200/v1/secret/data/{path}",
        headers={"X-Vault-Token": root_token},
    )
    return json.loads(urllib.request.urlopen(req).read())["data"]["data"]


def insecure_opener():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    import http.cookiejar

    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )


def check_unifi(targets, secrets):
    hits = []
    opener = insecure_opener()
    payload = json.dumps(
        {"username": secrets["unifi_username"], "password": secrets["unifi_password"]}
    ).encode()
    req = urllib.request.Request(
        f"{UNIFI_URL}/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener.open(req)

    req2 = urllib.request.Request(
        f"{UNIFI_URL}/proxy/network/api/s/default/rest/portforward",
        headers={"Accept": "application/json"},
    )
    data = json.loads(opener.open(req2).read())
    for rule in data["data"]:
        if rule.get("fwd") in targets:
            hits.append(f"Unifi port-forward '{rule.get('name')}' -> {rule.get('fwd')}:{rule.get('fwd_port')} (enabled={rule.get('enabled')})")
    return hits


def check_adguard(targets, secrets):
    hits = []
    req = urllib.request.Request(f"{ADGUARD_URL}/control/rewrite/list")
    req.add_header(
        "Authorization",
        "Basic "
        + __import__("base64")
        .b64encode(f"{secrets['adguard_user']}:{secrets['adguard_password']}".encode())
        .decode(),
    )
    data = json.loads(urllib.request.urlopen(req).read())
    for entry in data:
        if entry.get("answer") in targets:
            hits.append(f"AdGuard rewrite '{entry.get('domain')}' -> {entry.get('answer')}")
    return hits


def check_npm(targets, secrets):
    hits = []
    payload = json.dumps(
        {"identity": secrets["homepage_npm_email"], "secret": secrets["homepage_npm_password"]}
    ).encode()
    req = urllib.request.Request(
        f"{NPM_URL}/api/tokens", data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    token = json.loads(urllib.request.urlopen(req).read())["token"]

    req2 = urllib.request.Request(
        f"{NPM_URL}/api/nginx/proxy-hosts", headers={"Authorization": f"Bearer {token}"}
    )
    hosts = json.loads(urllib.request.urlopen(req2).read())
    for host in hosts:
        if host.get("forward_host") in targets:
            domains = ", ".join(host.get("domain_names", []))
            hits.append(f"NPM proxy host '{domains}' -> {host.get('forward_host')}:{host.get('forward_port')}")
    return hits


def check_repo(targets):
    # Source files only - .tfstate/.tfplan are single-line minified JSON
    # blobs (a single match prints the *entire file* as one line) and can
    # contain real plaintext values for some resource attributes. Never
    # grep those directly; `tofu show` on a real plan is the safe way to
    # inspect state if that's ever actually needed.
    hits = []
    patterns = "|".join(re.escape(t) for t in targets)
    try:
        result = subprocess.run(
            [
                "grep", "-rn", "-E", patterns,
                "--include=*.md", "--include=*.yml", "--include=*.yaml",
                "--include=*.py", "--include=*.j2", "--include=*.tf",
                f"{REPO_ROOT}/docs", f"{REPO_ROOT}/ansible",
                f"{REPO_ROOT}/scripts", f"{REPO_ROOT}/tofu",
            ],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            hits.append(line.replace(REPO_ROOT + "/", ""))
    except Exception as exc:
        hits.append(f"(repo grep failed: {exc})")
    return hits


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    targets = sys.argv[1:]
    secrets = load_secrets()

    checks = [
        ("Unifi port-forwards", lambda: check_unifi(targets, secrets)),
        ("AdGuard DNS rewrites", lambda: check_adguard(targets, secrets)),
        ("NPM proxy hosts", lambda: check_npm(targets, secrets)),
        ("Repo (docs/ansible/scripts/tofu)", lambda: check_repo(targets)),
    ]

    print(f"Checking for stale references to: {', '.join(targets)}\n")
    any_hits = False
    for name, fn in checks:
        try:
            hits = fn()
        except urllib.error.URLError as exc:
            print(f"[{name}] ERROR: could not reach - {exc}")
            continue
        except Exception as exc:
            print(f"[{name}] ERROR: {exc}")
            continue
        if hits:
            any_hits = True
            print(f"[{name}] {len(hits)} hit(s):")
            for h in hits:
                print(f"  - {h}")
        else:
            print(f"[{name}] clean")
        print()

    if any_hits:
        print("Real references found - resolve these before treating the IP as fully retired.")
        sys.exit(1)
    print("No stale references found in any checked system.")


if __name__ == "__main__":
    main()
