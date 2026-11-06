# K3s cluster nodes - six unprivileged LXCs spread across both hypervisors.
#
#   control plane (embedded etcd, 3 for quorum): k3s-cp1 (pve1), k3s-cp2 + k3s-cp3 (pve2)
#   workers: k3s-worker1 (pve1), k3s-worker2 (pve2), k3s-gpu1 (pve1, privileged, GPU-ready)
#
# Losing either hypervisor leaves etcd with quorum and a worker on the other
# node. K3s itself is installed and joined by the `k3s` Ansible role
# (ansible/playbooks/k3s.yml); MetalLB hands out service IPs from a reserved
# range on the same VLAN. See docs/k3s.md.

# ── K3s Control Plane LXCs ────────────────────────────────────────────────────
module "k3s_cp1" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "k3s-cp1"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 2048
  disk_size     = 15
  vlan_tag      = 30
  ip            = "10.20.30.24/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 45 # K3s Tier — between openbao/seal (30) and monitoring (40)
}

module "k3s_cp2" {
  source        = "./modules/proxmox_lxc"
  node          = "pve2"
  hostname      = "k3s-cp2"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 2048
  disk_size     = 15
  vlan_tag      = 30
  ip            = "10.20.30.25/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 45
}

module "k3s_cp3" {
  source        = "./modules/proxmox_lxc"
  node          = "pve2"
  hostname      = "k3s-cp3"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 2
  memory        = 2048
  disk_size     = 15
  vlan_tag      = 30
  ip            = "10.20.30.26/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 45
}

# ── K3s Worker LXCs (general) ──────────────────────────────────────────────────
module "k3s_worker1" {
  source        = "./modules/proxmox_lxc"
  node          = "pve1"
  hostname      = "k3s-worker1"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 4
  memory        = 4096
  disk_size     = 30
  vlan_tag      = 30
  ip            = "10.20.30.27/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 55 # K3s Tier — between monitoring/apps (40-50) and media (60)
}

module "k3s_worker2" {
  source        = "./modules/proxmox_lxc"
  node          = "pve2"
  hostname      = "k3s-worker2"
  dns_domain    = "example.lab"
  dns_servers   = ["10.20.1.10"]
  vcpus         = 4
  memory        = 4096
  disk_size     = 30
  vlan_tag      = 30
  ip            = "10.20.30.28/24"
  gateway       = "10.20.30.1"
  ostemplate    = var.debian12_lxc_template
  ssh_keys      = var.ssh_public_keys
  unprivileged  = true
  startup_order = 55
}

# ── k3s-gpu1: K3s GPU Worker LXC (pve1) ──────────────────────────────────────────
#
# Privileged LXC reserved for GPU-accelerated Kubernetes workloads. No device
# passthrough initially (both pve1 GPUs are allocated: RTX 4000 → tdarr LXC,
# Tesla M40 → ollama-m40 LXC). Add device_passthrough entries when a GPU
# becomes available or is freed up. NFS mount included to support media/ML
# datasets.
#
# Requires root@pam auth for privileged mode and the NFS feature (same as
# tdarr.tf). Was a standalone resource until 2026-10-15; moved into it in
# state with a moved {} block, removed once applied.
module "k3s_gpu1" {
  source             = "./modules/proxmox_lxc"
  providers          = { proxmox = proxmox.root_auth }
  node               = "pve1"
  hostname           = "k3s-gpu1"
  description        = "Managed by OpenTofu - K3s GPU worker node"
  tags               = ["opentofu", "k3s", "gpu"]
  vcpus              = 4
  memory             = 8192
  disk_size          = 30
  vlan_tag           = 30
  ip                 = "10.20.30.29/24"
  gateway            = "10.20.30.1"
  ostemplate         = var.debian12_lxc_template
  ssh_keys           = var.ssh_public_keys
  unprivileged       = false
  enable_nfs_feature = true
  startup_order      = 55 # K3s Tier — same as other K3s workers
  mount_points = [
    { volume = "/mnt/pve/tank-media", path = "/media" }
  ]
}
