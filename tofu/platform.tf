# Platform services the rest of the fleet depends on: secrets (OpenBao),
# SSO (Authentik), the internal certificate authority (step-ca) and the
# reverse proxy (Traefik).

# ── OpenBao LXC (secrets manager, main instance) ──────────────────────────────
# Moved to pve2 2026-08-03 (Known Issue #89): pve1 crashed on 2026-08-01
# restarting its NVIDIA GPU-passthrough LXCs (tdarr/ollama-m40), which
# sealed both OpenBao instances for 2 days before anyone noticed. pve2 has
# no NVIDIA GPU passthrough (its only GPU LXC is Intel QSV, stopped), so
# OpenBao no longer shares a host with that crash risk.
module "openbao" {
  source        = "./modules/proxmox_lxc"
  node          = "pve2"
  hostname      = "openbao"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 2048
  disk_size     = 15
  vlan_tag      = 30
  ip            = "10.20.30.22/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 30 # Tier 2 — needs openbao-seal auto-unseal
}

# Authentik SSO/identity provider - Postgres + server + worker containers.
# Sized 2 vCPU/4GB rather than the officially-recommended 2GB minimum -
# n8n's OOM (Known Issue #99, same night) was a direct result of sizing to
# the bare minimum for a multi-process stack.
module "authentik" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "authentik"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 4096
  disk_size     = 20
  vlan_tag      = 30
  ip            = "10.20.30.38/24" # confirmed free via live ping sweep
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 45 # Tier 3.5 — after monitoring, before general app services (relies on nothing, but other services may want SSO)
}

# step-ca - internal PKI/CA, decided 2026-08-24 (docs/research.md). Two
# concrete drivers: (1) fixes the already-open "Proxmox HTTPS certs still
# self-signed" security-sweep item, (2) internal-only hostnames stop
# leaking into public Certificate Transparency logs (the public wildcard
# ACME process publishes every issued subdomain to crt.sh regardless of
# VLAN/firewall isolation - free service-inventory reconnaissance today).
# Single container, light workload - sized like traefik, not authentik/netbox.
module "step_ca" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "step-ca"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 1
  memory        = 1024
  disk_size     = 10
  vlan_tag      = 30
  ip            = "10.20.30.47/24" # confirmed free via live check
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 45 # Tier 3.5 — after monitoring, before general app services (relies on nothing)
}

# Traefik - NPM replacement, Phase 1 of the migration decided 2026-08-23
# (see docs/todo.md, Known Issue #111/#112). Runs alongside NPM,
# untouched/live, until Phase 2 (DNS cutover) and Phase 3 (decommission)
# happen in later sessions - deliberately not a same-day rip-and-replace
# given 30+ live services depend on the thing being replaced.
module "traefik" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "traefik"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 1
  memory        = 1024
  disk_size     = 10
  vlan_tag      = 30
  ip            = "10.20.30.46/24" # confirmed free via live check
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 20 # Tier 1 — reverse proxy, same tier as NPM during Phase 1
}
