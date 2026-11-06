# Monitoring stack - each component in its own small LXC so one can be
# rebuilt or resized without touching the others. Configured by the
# prometheus / alertmanager / loki / grafana Ansible roles (monitoring.yml).

# ── Prometheus LXC ──────────────────────────────────────────────────────────
module "prometheus" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "prometheus"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 4 # live-resized during the KI #158 crash-loop recovery
  memory        = 4096
  disk_size     = 60 # KI #158: disk-full -> SIGBUS crash loop at 20G
  vlan_tag      = 30
  ip            = "10.20.30.12/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 40 # Tier 3 — monitoring
}

# ── Alertmanager LXC ─────────────────────────────────────────────────────────
# Real alerting, not another ad-hoc script - Prometheus's own rule_files/
# alerting.alertmanagers config sat unused (empty rules dir, alertmanagers:
# []) despite Prometheus being deployed since early in this fleet's build.
# Decided 2026-08-21 after NZBGet sat paused 19h unnoticed the same night -
# see docs/known-issues.md #104 and docs/todo.md.
module "alertmanager" {
  source              = "./modules/proxmox_lxc"
  node                = "pve1"
  hostname            = "alertmanager"
  dns_domain          = "example.lab"
  dns_servers         = ["10.20.1.10"]
  vcpus               = 1
  memory              = 512
  disk_size           = 10
  vlan_tag            = 30
  ip                  = "10.20.30.43/24" # confirmed free via live check
  gateway             = "10.20.30.1"
  ostemplate          = var.debian12_lxc_template
  ssh_keys            = var.ssh_public_keys
  unprivileged        = true
  hook_script_file_id = "templates:snippets/lxc-baseline.sh"
  providers           = { proxmox = proxmox.root_auth }
  startup_order       = 40 # Tier 3 — monitoring, alongside prometheus
}

# ── Loki LXC ────────────────────────────────────────────────────────────────
module "loki" {
  source        = "./modules/proxmox_lxc"
  node          = "pve2"
  hostname      = "loki"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 2048
  disk_size     = 30
  vlan_tag      = 30
  ip            = "10.20.30.13/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 20 # Tier 1 on pve2 — logging
}

# ── Grafana LXC ─────────────────────────────────────────────────────────────
module "grafana" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "grafana"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 1024
  disk_size     = 10
  vlan_tag      = 30
  ip            = "10.20.30.14/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 40 # Tier 3 — monitoring
}
