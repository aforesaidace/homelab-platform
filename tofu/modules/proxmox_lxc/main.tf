terraform {
  required_providers {
    proxmox = {
      source  = "registry.terraform.io/bpg/proxmox"
      version = "~> 0.73"
    }
  }
}

resource "proxmox_virtual_environment_container" "this" {
  node_name   = var.node
  description = var.description
  tags        = var.tags

  unprivileged        = var.unprivileged
  started             = true
  hook_script_file_id = var.hook_script_file_id

  initialization {
    hostname = var.hostname

    ip_config {
      ipv4 {
        address = var.ip
        gateway = var.gateway
      }
    }

    user_account {
      keys = [var.ssh_keys]
    }

    dynamic "dns" {
      for_each = var.dns_domain != null ? [1] : []
      content {
        domain  = var.dns_domain
        servers = var.dns_servers
      }
    }
  }

  cpu {
    cores = var.vcpus
  }

  memory {
    dedicated = var.memory
    swap      = 0
  }

  disk {
    datastore_id = "local-lvm"
    size         = var.disk_size
  }

  network_interface {
    name     = "eth0"
    vlan_id  = var.vlan_tag
    bridge   = "vmbr0"
    firewall = false
  }

  operating_system {
    template_file_id = var.ostemplate
    type             = "debian"
  }

  features {
    nesting = true
    mount   = var.enable_nfs_feature ? ["nfs"] : null
  }

  dynamic "mount_point" {
    for_each = var.mount_points
    content {
      volume = mount_point.value.volume
      path   = mount_point.value.path
    }
  }

  dynamic "device_passthrough" {
    for_each = var.device_passthrough
    content {
      path       = device_passthrough.value.path
      uid        = device_passthrough.value.uid
      gid        = device_passthrough.value.gid
      mode       = device_passthrough.value.mode
      deny_write = device_passthrough.value.deny_write
    }
  }

  dynamic "startup" {
    for_each = var.startup_order != null ? [1] : []
    content {
      order    = var.startup_order
      up_delay = var.startup_up_delay
    }
  }

  lifecycle {
    ignore_changes = [
      operating_system[0].template_file_id,
      mount_point,
      # Proxmox doesn't expose SSH-key/password config back through a
      # readable API field, so a freshly re-imported resource (e.g. after
      # a live pct migrate) has no user_account data in state at all,
      # while long-lived resources retain a stale password from creation
      # time - either way, this block is create-only. See Known Issue #61.
      initialization[0].user_account,
    ]
  }
}