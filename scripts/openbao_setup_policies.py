#!/usr/bin/env python3
"""
openbao_setup_policies.py — Create per-Ansible-role least-privilege OpenBao
policies + AppRoles, replacing the single broad `ansible-kv` policy (scoped
to all of secret/data/*) that every role would otherwise share.

Idempotent: policies and AppRole configs are PUT/POST as full desired state
(OpenBao treats both as overwrites, so re-running is safe). secret_ids are
NOT regenerated on a re-run unless --rotate is passed, since redistributing
a new one invalidates the old for anyone already using it.

Role -> KV path mapping was derived from grepping which Ansible role/template
actually references each secret key (not guessed) - see docs/known-issues.md
for the session that built this. `homepage` is the one genuine multi-path
role: its own services.yaml.j2 template reads nzbget_*/qbittorrent_*/
seerr_api_key directly (not homepage_*-prefixed copies), unlike every other
widget credential which does have its own homepage_* copy.

Roles with a KV path but currently zero Ansible consumers (open-webui,
uptime-kuma, unifi, openbao's own seal token) are deliberately skipped -
no policy is created until something actually needs to read it.

Usage:
    export BAO_TOKEN=<root or admin token>   # required (secrets.yml is retired)
    python3 scripts/openbao_setup_policies.py [--dry-run] [--rotate]
    bao-login && python3 scripts/openbao_setup_policies.py --ids-only
        # rebuild only the *_role_id entries in the secret_ids file from your
        # OIDC login (bao-admin has read on auth/approle/role/+/role-id). Writes
        # nothing to OpenBao; keeps every stored secret_id.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

import yaml

OPENBAO_ADDR = "http://10.20.30.22:8200"

# Role -> list of KV paths it needs read access to. Confirmed via
# `grep -rl <secret_key> ansible/roles/ ansible/playbooks/`, not guessed.
ROLE_PATHS = {
    # unifi: used by adguard_cutover.yml (DHCP DNS cutover) - initially
    # missed, found 2026-08-04 when actually migrating adguard's playbooks.
    "adguard": ["adguard", "unifi"],
    # homepage: the amp role's `ampinstmgr quickstart` task sets AMP's admin
    # password from homepage_amp_password directly (shared value with the
    # Homepage widget, not a separate amp-owned secret) - found 2026-08-04.
    "amp": ["amp", "homepage"],
    "arr": [
        "arr", "discord", "mullvad", "seerr", "jellyfin", "plex",
        "qbittorrent", "nzbget", "newshosting", "nzbgeek",
    ],
    "npm": ["cloudflare"],
    "traefik": ["cloudflare"],
    "gitea": ["gitea"],
    "grafana": ["grafana"],
    # paperless: Homepage's paperlessngx widget needs paperless_admin_user/
    # password directly - added 2026-08-04, same cross-role pattern as
    # nzbget/qbittorrent/seerr above.
    "homepage": ["homepage", "nzbget", "qbittorrent", "seerr", "paperless"],
    "n8n": ["n8n"],
    # postgres: the shared Postgres LXC's playbook reads per-app DB
    # passwords from secret/postgres-shared to provision each database in
    # the postgres_shared_databases loop. Added 2026-09-06, audiobook Phase 3
    # (audiobookrequest is the first entry; chaptarr follows in Phase 4).
    "postgres": ["postgres-shared"],
    "patchmon": ["patchmon"],
    "pbs": ["pbs"],
    "truenas": ["truenas"],
    "paperless": ["paperless"],
    "authentik": ["authentik", "freeipa"],
    "fleet-monitor": ["fleet-monitor"],
    # netbox: decommissioned 2026-09-13 (Phase 4) - its secret/netbox KV
    # path + netbox-kv AppRole are torn down separately (Stage 5).
    # nautobot: NetBox replacement (SoT tool decided 2026-09-09). Vault-first
    # from day one like netbox - the nautobot role reads pg/secret_key/
    # superuser creds from secret/nautobot. See docs/todo.md Initiative 1.
    "nautobot": ["nautobot"],
    "freeipa": ["freeipa"],
    "alertmanager": ["alertmanager"],
    "freshrss": ["freshrss"],
    "step-ca": ["step-ca"],
    # searxng: server.secret_key only (settings.yml has no other secret -
    # no login system exists to have credentials for). Decided 2026-08-24,
    # built 2026-09-13.
    "searxng": ["searxng"],
    # audiobookshelf: Authentik OIDC client_id/client_secret + issuer URL
    # (secret/audiobookshelf). ABS's OIDC config lives in its own DB, set
    # via its API - the audiobookshelf role reads these from vault and
    # PATCHes ABS's settings. Added 2026-09-05, audiobook-stack Phase 2.
    "audiobookshelf": ["audiobookshelf"],
    # harbor: admin + DB passwords + (later) OIDC client_id/secret
    # (secret/harbor). Registry/pull-through-cache/Trivy platform - see
    # docs/research.md. Added 2026-09-06.
    "harbor": ["harbor"],
    # audiobookrequest: OIDC client_id/secret + issuer, the Discord webhook
    # URL, the ABS API token, the break-glass init-root password, and its
    # own copy of the Postgres password (secret/audiobookrequest). The DB
    # password is also mirrored into secret/postgres-shared for the postgres
    # playbook - same two-copy pattern as actual_categorizer, so no app can
    # read another app's DB creds. Added 2026-09-06, audiobook Phase 3.
    "audiobookrequest": ["audiobookrequest"],
    "snmp-exporter": ["snmp-exporter"],
    "bookstack": ["bookstack"],
    # dashy: the AdGuard-stats widget needs adguard_user/adguard_password
    # directly - found 2026-08-25, previously hardcoded plaintext in
    # ansible/roles/dashy/defaults/main.yml (committed to git, unlike
    # secrets.yml) and went stale the moment adguard's password rotated.
    # dashy: also needs its own path now (OIDC client_id, 2026-08-26) -
    # a public OIDC client has no secret, but the client_id is still
    # config that shouldn't live hardcoded in the role's own defaults.
    "dashy": ["adguard", "dashy"],
    # actual: server password was set once via the first-run web UI with
    # no env var for non-interactive bootstrap (docs/vms-lxcs.md) - never
    # had a vault path at all until reset 2026-08-25. Will also back the
    # jhonderson/actual-http-api wrapper once that's built (the wrapper
    # holds this password internally, issues its own separate API key to
    # the categorization script - see docs/todo.md).
    "actual": ["actual"],
    # actual_categorizer: the categorization script's own narrow AppRole,
    # deliberately separate from actual-kv above (which also grants read
    # on the real actual_password) - matches the automation-tier principle
    # from the RBAC taxonomy design (docs/todo.md), the exact scenario
    # that motivated it. secret/actual_categorizer holds a copy of the
    # same actual_http_api_key value, not actual_password.
    "actual_categorizer": ["actual_categorizer"],
    # homeassistant: not an Ansible-deployed secret (HA itself is a
    # hand-managed appliance, docs/infrastructure.md) - this is a personal
    # long-lived access token used for direct API calls (curl/scripts),
    # same "narrow AppRole for direct tool access" pattern as
    # actual_categorizer above rather than a real Ansible role consumer.
    "homeassistant": ["homeassistant"],
    # pve_exporter: read-only Proxmox API token (PVEAuditor role, scoped
    # via pveum, not root@pam) for the pve_exporter role built 2026-09-01
    # after a real PBS-storage outage went unnoticed for 2+ days with no
    # alerting on it (docs/todo.md). Narrow AppRole, own KV path - same
    # pattern as every other role here, not reusing an existing one.
    "pve_exporter": ["pve_exporter"],
    # eve-online: ~/eve/eve-mentor/launch.sh reads the eve-mentor SSO Client
    # ID at MCP-server start. Was created by hand once and lost with workstation in
    # the 2026-09-29 rebuild - defined here so it can't be lost again.
    "eve-online": ["eve-online"],
    # drift-check: the weekly doc-vs-infra reconciliation timer on the n8n
    # LXC (ansible/roles/drift_check/, built 2026-09-03). Reuses the
    # existing PVEAuditor token in secret/proxmox (proxmox2netbox_*,
    # whole-cluster read) rather than minting a new one, plus secret/homepage
    # for homepage_npm_email/password (the real NPM admin login).
    # secret/drift-check added 2026-09-13 (Phase 3(c), docs/todo.md
    # Initiative 1) - the one drift-check-owned secret: a dedicated
    # read-only Nautobot user/token (view-only on
    # virtualization.virtualmachine/extras.job/extras.jobresult), not a
    # reuse of anything else's credential.
    "drift-check": ["proxmox", "homepage", "drift-check"],
    # tofu: OpenTofu's own OpenBao access (main.tf's `provider "vault"`) -
    # first time Tofu consumes a secret from OpenBao rather than just
    # managing OpenBao's own resources (openbao.tfplan). Read-only on
    # secret/unifi (already populated, shared with unifi2netbox) and the
    # new secret/tofu (proxmox_api_token, proxmox_pve1_root_password -
    # moved off tofu/terraform.tfvars 2026-09-12). role_id/secret_id get
    # synced out of ansible/.vault_secret_ids.yml into
    # tofu/vault_secret_ids.auto.tfvars by scripts/tofu_vault_sync_secret_id.py
    # (Tofu can't read this file's YAML directly). tofu-state added
    # 2026-10-01: the pg backend's login (lost with secrets.yml), read by
    # scripts/tofu_init.sh to build -backend-config - so this one role is
    # everything a Tofu run needs. azure added 2026-10-01: the homelab-tofu
    # service principal + IDs for the Key Vault auto-unseal resources
    # (written by scripts/azure_unseal_bootstrap.sh).
    # cloudflare added 2026-10-07: tofu/cloudflare_mail.tf manages the public
    # mail DNS records (Migadu) with the same token NPM/Traefik use for ACME.
    "tofu": ["tofu", "unifi", "tofu-state", "azure", "cloudflare"],
    # netalertx: new-device/IP-conflict scanner (Known Issue #90, Initiative
    # 3). secret/netalertx holds its own generated API_TOKEN (metrics/GraphQL
    # bearer auth) and the Discord webhook URL for its native WEBHOOK
    # publisher. The UniFi API import plugin's key is deliberately configured
    # live through NetAlertX's own Settings UI instead - its config shape is
    # a nested per-site array the settings system expects via its own API/UI,
    # not a flat app.conf key like the two above, so it isn't vault-sourced.
    "netalertx": ["netalertx"],
    # atuin: self-hosted E2E-encrypted shell history sync (Initiative 3).
    # secret/atuin holds its own copy of the shared-Postgres DB password
    # (also mirrored into secret/postgres-shared for provisioning - same
    # two-copy pattern as audiobookrequest/nautobot, so no app can read
    # another app's DB credential). No account/API secret beyond that -
    # registration is username/password set up by the user directly
    # against the running server, and the actual history-encryption key
    # never leaves the client by design.
    "atuin": ["atuin"],
    "mealie": ["mealie"],       # Mealie recipe app (2026-10-15)
    "transcriber": ["transcriber"],   # Signal -> Karakeep transcriber (2026-10-27)
    "omniroute": ["omniroute"],   # OmniRoute AI gateway (2026-10-09)
    # immich: family photo/video library (Initiative-adjacent, decided
    # 2026-09-15). secret/immich holds the dedicated Postgres password
    # (this app needs its own vector-extension Postgres, doesn't fit the
    # shared instance) and the Authentik OIDC client_id/secret.
    "immich": ["immich"],
    # nextcloud: household file storage. Never migrated off secrets.yml;
    # moved 2026-10-01 when secrets.yml was retired (lost in the workstation wipe).
    # secret/nextcloud holds the admin, Postgres and Redis passwords.
    "nextcloud": ["nextcloud"],
    # claude-readonly: standing read access for Claude Code's own use in
    # this repo, not tied to any one Ansible role. User-approved 2026-09-19:
    # "you should be able to read only from the vault, unless with explicit
    # permission to write to it" - homelab-only secrets, no PHI/regulated
    # data, so a broad read scope is acceptable in exchange for not needing
    # a fresh credential handed over for every ad-hoc lookup. `["*"]` here
    # expands (via policy_hcl) to `secret/data/*` / `secret/metadata/*` -
    # every KV path, read+list only, no write/delete capability at all
    # (enforced by OpenBao's own ACL, not just convention). Paired with
    # `scripts/vault_read.py` (the only thing meant to use this AppRole)
    # and a Claude Code settings.json Bash allow-rule scoped to that one
    # script - see its own docstring for the full loop.
    "claude-readonly": ["*"],
    # jenkins: Phase 1 of the Jenkins-on-K3s ephemeral CI build (decided
    # 2026-09-22, docs/todo.md Initiative 1 - the Gitea/Forgejo CVE-driven
    # CI work). Scoped narrowly to secret/jenkins only, matching this repo's
    # per-app AppRole convention - not a broad K8s-wide credential. Two
    # planned consumers, per the hardening principles already decided:
    # (1) the Jenkins controller itself, via the official HashiCorp Vault
    # plugin (`withVault` block) for its own secrets (webhook shared secret,
    # etc.); (2) potentially External Secrets Operator's ClusterSecretStore
    # for agent-pod-level secrets, though ESO's own AppRole scope is still
    # unscoped (Phase 2, not yet built) and may end up separate from this
    # one if ESO grows beyond Jenkins as a consumer.
    "jenkins": ["jenkins"],
    # Added 2026-10-01 for the secrets.yml retirement (docs/todo.md Critical
    # step 3) - the last playbooks that still loaded group_vars/secrets.yml:
    # uptime-kuma: monitoring.yml's Uptime Kuma admin login (secret/uptime-kuma).
    "uptime-kuma": ["uptime-kuma"],
    # hermes-agent: Signal account + allowed users for the Hermes gateway
    # (captured from the live .env by scripts/grafana_k3s-cp3_to_openbao.sh).
    "hermes-agent": ["hermes-agent"],
    # tofu-state: tofu_state.yml's Postgres login (same secret Tofu's own
    # tofu-kv role reads for the backend).
    "tofu-state": ["tofu-state"],
    # openbao: openbao.yml's transit seal token (secret/openbao) - wired when
    # the Azure Key Vault seal work resumes.
    "openbao": ["openbao"],
    # media-pi: playbooks/media-pi.yml (Plasma Bigscreen Pi 5) - the Wi-Fi
    # passphrase (secret/wifi) and ops's sudo password (secret/media-pi,
    # used as ansible_become_password; added 2026-10-13).
    "media-pi": ["wifi", "media-pi"],
}

# Mirrors the existing `ansible` AppRole's live config (checked 2026-08-04)
# for consistency, minus the broader token_policies/capabilities.
APPROLE_DEFAULTS = {
    "token_ttl": 3600,
    "token_max_ttl": 14400,
    "secret_id_num_uses": 0,
    "secret_id_ttl": 0,
    "bind_secret_id": True,
}

SECRET_IDS_FILE = "ansible/.vault_secret_ids.yml"


def get_token(allow_login_token=False):
    token = os.environ.get("BAO_TOKEN")
    if not token and allow_login_token:
        # --ids-only only: the `bao-login` (OIDC) token in ~/.vault-token is enough.
        try:
            token = open(os.path.expanduser("~/.vault-token")).read().strip()
        except OSError:
            token = None
    if not token:
        sys.exit("Set BAO_TOKEN first (root/admin token) - secrets.yml is retired")
    return token


def call(token, method, path, body=None):
    url = OPENBAO_ADDR + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"X-Vault-Token": token}, method=method
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw else {})


# Roles needing write (not just read) access - deliberately rare and kept
# separate from ROLE_PATHS so it's never accidentally treated as the norm.
# First entry: credential rotation (added 2026-09-23 after 3 separate real
# leaks this session). Originally built for a fully-automated rotation
# script; that script was abandoned in favor of a recurring weekly
# human+Claude rotation session instead (see docs/todo.md), but this
# AppRole is kept - it's legitimate reusable infrastructure for that
# session to write freshly-rotated values back with, not tied to any one
# script. Every other AppRole in this file is read-only by design; this is
# a genuine, narrow exception (create/update only, no delete - a rotation
# replaces a value, never removes the secret entirely), not a precedent to
# reuse casually.
WRITE_ROLE_PATHS = {
    "rotation": ["arr", "seerr"],
}


def policy_hcl(paths, write=False):
    blocks = []
    data_capabilities = '["create", "update", "read"]' if write else '["read"]'
    for path in paths:
        blocks.append(
            f'path "secret/data/{path}" {{\n  capabilities = {data_capabilities}\n}}\n'
            f'path "secret/metadata/{path}" {{\n  capabilities = ["read", "list"]\n}}'
        )
    return "\n".join(blocks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rotate", action="store_true",
        help="Generate a fresh secret_id even for roles that already have one stored",
    )
    parser.add_argument(
        "--ids-only", action="store_true",
        help="Only refresh the *_role_id entries from OpenBao (read-only); keep every secret_id",
    )
    args = parser.parse_args()

    token = get_token(allow_login_token=args.ids_only)

    existing_ids = {}
    if os.path.exists(SECRET_IDS_FILE):
        with open(SECRET_IDS_FILE) as f:
            existing_ids = yaml.safe_load(f) or {}

    results = dict(existing_ids)

    all_roles = [(role, paths, False) for role, paths in ROLE_PATHS.items()]
    all_roles += [(role, paths, True) for role, paths in WRITE_ROLE_PATHS.items()]

    if args.ids_only:
        fresh = dict(existing_ids)
        for role, _paths, _write in all_roles:
            status, data = call(token, "GET", f"/v1/auth/approle/role/{role}-kv/role-id")
            role_id = data.get("data", {}).get("role_id")
            if not role_id:
                sys.exit(f"{role}: role-id read {status} - {SECRET_IDS_FILE} left untouched")
            if not existing_ids.get(f"{role}_secret_id"):
                # Issuing a secret_id needs root (deliberately not in bao-admin).
                print(f"  warning: {role} has no stored secret_id (run without --ids-only as root)")
            fresh[f"{role}_role_id"] = role_id
        if args.dry_run:
            print(f"[dry-run] would refresh {len(all_roles)} role_ids")
            return
        if os.path.exists(SECRET_IDS_FILE):
            os.replace(SECRET_IDS_FILE, SECRET_IDS_FILE + ".bak")
        with open(SECRET_IDS_FILE, "w") as f:
            yaml.safe_dump(fresh, f, default_flow_style=False)
        os.chmod(SECRET_IDS_FILE, 0o600)
        print(f"Refreshed {len(all_roles)} role_ids in {SECRET_IDS_FILE} (secret_ids untouched).")
        return

    for role, paths, write in all_roles:
        policy_name = f"{role}-kv"
        approle_name = f"{role}-kv"

        print(f"=== {role} -> {paths}{' (write)' if write else ''} ===")

        if args.dry_run:
            print(f"  [dry-run] would PUT policy {policy_name}")
            print(f"  [dry-run] would POST approle {approle_name}")
            continue

        status, _ = call(
            token, "PUT", f"/v1/sys/policies/acl/{policy_name}",
            {"policy": policy_hcl(paths, write=write)},
        )
        print(f"  policy {policy_name}: {status}")
        if status not in (200, 204):
            # 2026-10-21: an OIDC bao-admin token (secret/* only) got 403 on every call
            # and the old code still overwrote the file with empty role_ids.
            sys.exit(f"policy write failed ({status}): this token can't administer "
                     f"sys/auth (needs the root token) - {SECRET_IDS_FILE} left untouched")

        status, _ = call(
            token, "POST", f"/v1/auth/approle/role/{approle_name}",
            {**APPROLE_DEFAULTS, "token_policies": [policy_name]},
        )
        print(f"  approle {approle_name}: {status}")

        status, data = call(
            token, "GET", f"/v1/auth/approle/role/{approle_name}/role-id"
        )
        role_id = data.get("data", {}).get("role_id")
        print(f"  role_id: {status} {role_id}")

        role_key = f"{role}_role_id"
        secret_key = f"{role}_secret_id"

        # An empty value counts as missing: --ids-only leaves blank secret_ids
        # for roles it couldn't issue (mealie/omniroute after 2026-10-21).
        need_secret_id = args.rotate or not existing_ids.get(secret_key)
        if need_secret_id:
            status, data = call(
                token, "POST", f"/v1/auth/approle/role/{approle_name}/secret-id"
            )
            secret_id = data.get("data", {}).get("secret_id")
            print(f"  secret_id: {status} (newly generated)")
        else:
            secret_id = existing_ids[secret_key]
            print("  secret_id: reused existing (pass --rotate to regenerate)")

        if not role_id or not secret_id:
            sys.exit(f"no role_id/secret_id for {role} - {SECRET_IDS_FILE} left untouched")
        results[role_key] = role_id
        results[secret_key] = secret_id

    if args.dry_run:
        return

    if os.path.exists(SECRET_IDS_FILE):
        os.replace(SECRET_IDS_FILE, SECRET_IDS_FILE + ".bak")
    with open(SECRET_IDS_FILE, "w") as f:
        yaml.safe_dump(results, f, default_flow_style=False)
    os.chmod(SECRET_IDS_FILE, 0o600)
    print(f"\nWrote {SECRET_IDS_FILE} (gitignored, 0600) with {len(results)} entries.")


if __name__ == "__main__":
    main()
