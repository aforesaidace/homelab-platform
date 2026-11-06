variable "node" {
  description = "Proxmox node name"
  type        = string
}

variable "description" {
  description = "Proxmox notes field. Defaults to the value every module-managed LXC already has; set it when folding in a standalone resource that had its own"
  type        = string
  default     = "Managed by OpenTofu"
}

variable "tags" {
  description = "Proxmox tags. Defaults to the value every module-managed LXC already has; set it when folding in a standalone resource that had its own"
  type        = list(string)
  default     = ["tofu"]
}

variable "hostname" {
  description = "LXC hostname"
  type        = string
}

variable "vcpus" {
  description = "Number of vCPU cores"
  type        = number
  default     = 1
}

variable "memory" {
  description = "RAM in MB"
  type        = number
  default     = 512
}

variable "disk_size" {
  description = "Root disk size (e.g. '8G')"
  type        = string
  default     = "8G"
}

variable "vlan_tag" {
  description = "VLAN tag for the network interface"
  type        = number
}

variable "ip" {
  description = "Static IPv4 address with CIDR (e.g. '10.20.30.16/24')"
  type        = string
}

variable "gateway" {
  description = "Default gateway IP"
  type        = string
}

variable "ostemplate" {
  description = "Proxmox LXC OS template identifier"
  type        = string
}

variable "ssh_keys" {
  description = "SSH public key(s) to inject"
  type        = string
}

variable "unprivileged" {
  description = "Run container as unprivileged"
  type        = bool
  default     = true
}

variable "mount_points" {
  description = "Optional bind-mount points (host NFS mounts) for the container"
  type = list(object({
    volume = string
    path   = string
  }))
  default = []
}

variable "enable_nfs_feature" {
  description = "Set the mount=nfs LXC feature flag (leftover from an earlier approach; harmless to leave set, but changing it requires root@pam session auth, not an API token - so we just match whatever the actual current state is per-container to avoid a blocked apply)"
  type        = bool
  default     = false
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

variable "dns_domain" {
  description = "Optional DNS search domain override (e.g. AdGuard fix from Known Issue #55). Leave null to omit."
  type        = string
  default     = null
}

variable "dns_servers" {
  description = "Optional DNS server list override (e.g. AdGuard fix from Known Issue #55). Leave null to omit."
  type        = list(string)
  default     = null
}

variable "device_passthrough" {
  description = "Optional list of host device nodes to pass through (e.g. GPU /dev/nvidia* nodes for hardware transcoding). Matches the manual pct config already applied by hand to tdarr/ollama-m40 (see docs/opentofu.md) - added here so new containers needing the same GPU access are IaC-managed instead of another one-off manual pct set."
  type = list(object({
    path       = string
    uid        = optional(number, 0)
    gid        = optional(number, 0)
    mode       = optional(string, "0660")
    deny_write = optional(bool, false)
  }))
  default = []
}

variable "hook_script_file_id" {
  description = "Optional storage file ID (e.g. 'templates:snippets/lxc-baseline.sh') of an executable Proxmox hookscript, run at container lifecycle phases (pre-start/post-start/pre-stop/post-stop). LXC equivalent of the VM cloud-init snippet - see tofu/modules/proxmox_vm's cloud_init_snippet. Deliberately opt-in (default null, existing containers unaffected) rather than fleet-wide-default like the VM module's cloud_init_snippet: a non-null default would force an in-place update across every existing LXC on the next unscoped apply, which conflicts with this repo's -target-scoped-apply convention (see AGENTS.md Security Considerations). Set explicitly per module block for new containers, or apply to existing ones deliberately via a scoped -target sweep if wanted. NOTE: like enable_nfs_feature/mount_points' bind type, setting this is root@pam-only (HTTP 403 via the default API-token provider) - any module call that sets this needs providers = { proxmox = proxmox.root_auth }, see the arr_core/arr_downloaders/arr_media blocks in tofu/main.tf for the pattern."
  type        = string
  default     = null
}