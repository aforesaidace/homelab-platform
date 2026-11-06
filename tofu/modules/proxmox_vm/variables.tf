variable "node" {
  description = "Proxmox node name"
  type        = string
}

variable "name" {
  description = "VM name"
  type        = string
}

variable "vcpus" {
  description = "Number of vCPU cores"
  type        = number
  default     = 2
}

variable "memory" {
  description = "RAM in MB"
  type        = number
  default     = 2048
}

variable "memory_floating" {
  description = "Ballooning floor in MB - if set, enables memory ballooning so Proxmox can reclaim unused guest RAM back down to this floor instead of permanently holding the guest's historical peak usage. Leave null to disable ballooning (fixed allocation, current default behavior)."
  type        = number
  default     = null
}

variable "disk_size" {
  description = "Root disk size in GB"
  type        = number
  default     = 20
}

variable "vlan_tag" {
  description = "VLAN tag for the network interface"
  type        = number
}

variable "ip" {
  description = "Static IPv4 address with CIDR"
  type        = string
}

variable "gateway" {
  description = "Default gateway IP"
  type        = string
}

variable "template" {
  description = "Proxmox VM template name or file ID"
  type        = string
}

variable "cloud_init_snippet" {
  description = "Cloud-init user-data snippet file ID - override for non-Debian templates (e.g. RHEL-family needs 'wheel' not 'sudo' as the admin group)"
  type        = string
  default     = "templates:snippets/qemu-guest-agent-ansible.yml" # account `ansible`; roles/proxmox_snippets
}

variable "ssh_keys" {
  description = "SSH public key(s) to inject"
  type        = string
}

variable "started" {
  description = "Whether the VM should be running. false keeps tofu from starting a VM that was shut down on purpose (amp, 2026-11-03)."
  type        = bool
  default     = true
}

variable "serial_console" {
  description = "Give the VM a serial port (serial0 socket). Debian's cloud images boot with console=ttyS0 and kernel-panic on first boot without one (\"Attempted to kill init\", found 2026-10-04 on devbox, reproduced on throwaway VMs). Also makes the boot log capturable via /var/run/qemu-server/<vmid>.serial0. Adding it to an existing VM reboots it."
  type        = bool
  default     = true
}

variable "startup_order" {
  description = "Optional Proxmox boot order. Leave null to omit the startup block entirely."
  type        = number
  default     = null
}

variable "startup_up_delay" {
  description = "Optional delay in seconds before the next VM/CT is started after this one. Only used when startup_order is set."
  type        = number
  default     = null
}

variable "pci_devices" {
  description = "List of PCI passthrough devices"
  type = list(object({
    id     = string
    pcie   = bool
    rombar = bool
  }))
  default = []
}
