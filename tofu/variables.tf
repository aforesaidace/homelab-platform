# Non-secret values are committed defaults so a workstation wipe can't lose them
# again (terraform.tfvars was lost 2026-09-29). Secrets come from OpenBao
# (vault_vars.tf); `scripts/tofu_init.sh` does the rest.
variable "proxmox_pve1_endpoint" {
  description = "HTTPS endpoint for Proxmox API on pve1"
  type        = string
  default     = "https://10.20.1.70:8006/"
}

variable "debian12_template" {
  description = "Volume ID of the Debian 12 cloud image on shared 'templates' NFS storage"
  type        = string
  default     = "templates:import/debian-12-generic-amd64.qcow2"
}

variable "rocky9_template" {
  description = "Volume ID of the Rocky Linux 9 GenericCloud image on shared 'templates' NFS storage - only non-Debian template in this fleet, added 2026-08-21 specifically because freeipa-server isn't packaged for Debian at all (native RPM install needs a RHEL-family OS)"
  type        = string
  default     = "templates:import/rocky-9-generic-cloud-amd64.qcow2"
}

variable "debian12_lxc_template" {
  description = "Proxmox LXC ostemplate string for Debian 12 on shared 'templates' NFS storage"
  type        = string
  default     = "templates:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
}

variable "windows11_iso" {
  description = "Volume ID of the Windows 11 x64 multi-edition ISO on shared 'templates' NFS storage - added 2026-09-05 for the one-off winadmin VM (Brother printer firmware update - no browser/EWS-only method exists). Update once the real filename is known after upload; no stable direct-download URL exists to pin like the Debian/Rocky cloud images above (Microsoft's own download page issues session-scoped signed links, not a static one)."
  type        = string
  default     = "templates:iso/Win11_25H2_English_x64_v2.iso"
}

variable "ssh_public_keys" {
  description = "SSH public keys to inject into VMs and LXCs (create-time only for LXCs - see the module's ignore_changes). Automation key (secret/ansible-ssh) + ops's personal key."
  type        = string
  default     = <<-EOT
    ssh-ed25519 AAAA_PLACEHOLDER_PUBLIC_KEY ansible@example 2026-09-30
    ssh-ed25519 AAAA_PLACEHOLDER_PUBLIC_KEY ops@workstation
  EOT
}

# proxmox_api_token / proxmox_pve1_root_password moved to OpenBao
# (secret/tofu) 2026-09-12 - see tofu/vault_vars.tf's data
# "vault_kv_secret_v2" "tofu".
