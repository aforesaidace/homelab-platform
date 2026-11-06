terraform {
  required_providers {
    proxmox = {
      source  = "registry.terraform.io/bpg/proxmox"
      version = "~> 0.73"
    }
    vault = {
      source  = "registry.terraform.io/hashicorp/vault"
      version = "~> 4.0"
    }
    cloudflare = {
      source  = "registry.terraform.io/cloudflare/cloudflare"
      version = "~> 5.27"
    }
  }

  # Migrated from backend "local" 2026-09-02 (docs/todo.md "OpenTofu state
  # durability") - the single unreplicated local file had no locking and no
  # backup coverage. Intentionally empty here, same pattern as before: the
  # real `conn_str` (points at the new dedicated tofu-state Postgres
  # instance, 10.20.30.61) is supplied via `-backend-config` at `tofu
  # init` time, both for local runs from workstation and for CI - never committed.
  # See Known Issue #53 for why CI needed its own override in the first
  # place (this backend's own locking now makes concurrent-run corruption
  # structurally harder too, not just the original "state gets wiped
  # between runner jobs" problem).
  backend "pg" {}
}

# Vault/OpenBao provider - first-ever secret consumption from OpenBao by
# OpenTofu itself (previously Tofu only *managed* OpenBao's own resources,
# see openbao.tfplan) - migrating unifi_username/password + proxmox_api_token
# + proxmox_pve1_root_password off tofu/terraform.tfvars, 2026-09-12. The
# hashicorp/vault provider talks to OpenBao fine - same Vault-compatible
# HTTP API community.hashi_vault already uses from the Ansible side.
# Dedicated least-privilege tofu-kv AppRole (read-only on secret/unifi +
# secret/tofu, scripts/openbao_setup_policies.py); role_id/secret_id
# bootstrap themselves live in tofu/vault_secret_ids.auto.tfvars
# (gitignored, Terraform auto-loads *.auto.tfvars) - mirrors the
# .vault_secret_ids.yml fix already applied on the Ansible side after an
# env-var-based bootstrap broke a real deploy.
provider "vault" {
  address = "http://10.20.30.22:8200"
  # tofu-kv is a narrow, read-only AppRole (no auth/token/create capability
  # - least privilege, by design) - the provider's default behavior of
  # minting a scoped child token needs that capability, so skip it and use
  # the AppRole-derived token directly.
  skip_child_token = true

  auth_login {
    path = "auth/approle/login"
    parameters = {
      role_id   = var.tofu_kv_role_id
      secret_id = var.tofu_kv_secret_id
    }
  }
}

# Default provider - scoped tofu-agent@pve (PVEAdmin role, privsep enabled),
# not root@pam. Replaced 2026-08-26 - the previous root@pam!homelab-agent
# token had privsep:0 (full unrestricted root API access, no ACL
# restriction) despite Tofu never needing anything beyond VM/LXC/storage
# management. See docs/todo.md and docs/opentofu.md for the full writeup.
# root_auth below (root@pam, unchanged) stays for the handful of modules
# needing mount_points/hook_script_file_id, which Proxmox hard-walls to
# root@pam regardless of ACL role.
#
# SSH private_key switched from the personal rober_ed25519 to the
# automation ansible_ed25519 key 2026-09-23 - found live while building
# the Jenkins-on-K3s pipeline (Phase 6, docs/todo.md Initiative 1): the
# existing Gitea Actions deploy-tofu.yml workflow already injected the
# personal key as a CI secret, violating this repo's own stated rule
# ("never use the personal rober_ed25519 key for automation"). Fixed here
# rather than propagated into a second CI system - ansible_ed25519
# already has passwordless root SSH to pve1 (confirmed live), no new
# authorized_keys entry needed.
provider "proxmox" {
  endpoint  = var.proxmox_pve1_endpoint
  api_token = data.vault_kv_secret_v2.tofu.data["proxmox_api_token"]
  insecure  = true

  ssh {
    agent       = false
    username    = "root"
    private_key = file("~/.ssh/ansible_ed25519")
  }
}

provider "proxmox" {
  alias    = "root_auth"
  endpoint = var.proxmox_pve1_endpoint
  username = "root@pam"
  password = data.vault_kv_secret_v2.tofu.data["proxmox_pve1_root_password"]
  insecure = true

  ssh {
    agent       = false
    username    = "root"
    private_key = file("~/.ssh/ansible_ed25519")
  }
}
