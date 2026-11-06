terraform {
  required_providers {
    proxmox = {
      source  = "registry.terraform.io/bpg/proxmox"
      version = "~> 0.73"
    }
  }
}

# cloud-init meta-data with the VM's name. Our user-data snippet replaces the
# user-data Proxmox generates, and that is where Proxmox puts the hostname, so
# without this every VM booted as "localhost" (found 2026-11-03 via PatchMon).
# Written through pve1: the `templates` storage is shared NFS, pve2 sees it too.
resource "proxmox_virtual_environment_file" "meta_data" {
  content_type = "snippets"
  datastore_id = "templates"
  node_name    = "pve1"

  source_raw {
    file_name = "meta-${var.name}.yml"
    data      = <<-EOT
      instance-id: ${var.name}
      local-hostname: ${var.name}
    EOT
  }
}

resource "proxmox_virtual_environment_vm" "this" {
  node_name       = var.node
  name            = var.name
  description     = "Managed by OpenTofu"
  tags            = ["tofu"]
  started         = var.started
  stop_on_destroy = true

  cpu {
    cores   = var.vcpus
    sockets = 1
    type    = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.memory
    floating  = var.memory_floating
  }

  agent {
    enabled = true
    timeout = "3m"
  }

  operating_system {
    type = "l26"
  }

  network_device {
    bridge   = "vmbr0"
    vlan_id  = var.vlan_tag
    firewall = false
  }

  disk {
    datastore_id = "local-lvm"
    file_id      = var.template
    interface    = "scsi0"
    size         = var.disk_size
    discard      = "on"
    ssd          = true
  }

  initialization {
    user_data_file_id = var.cloud_init_snippet
    meta_data_file_id = proxmox_virtual_environment_file.meta_data.id

    ip_config {
      ipv4 {
        address = var.ip
        gateway = var.gateway
      }
    }

    user_account {
      # Create-only and ignored by user_data_file_id anyway: the snippet defines the account.
      username = "ansible"
      keys     = [var.ssh_keys]
    }
  }

  dynamic "serial_device" {
    for_each = var.serial_console ? [1] : []
    content {
      device = "socket"
    }
  }

  dynamic "startup" {
    for_each = var.startup_order != null ? [1] : []
    content {
      order    = var.startup_order
      up_delay = var.startup_up_delay
    }
  }

  dynamic "hostpci" {
    for_each = var.pci_devices
    content {
      device  = "hostpci${hostpci.key}"
      id      = hostpci.value.id
      pcie    = hostpci.value.pcie
      rombar  = hostpci.value.rombar
      mapping = null
    }
  }

  lifecycle {
    ignore_changes = [
      # Create-only, same as modules/proxmox_lxc. Rewriting cloud-init data
      # on a running VM changes its instance-id, so the next reboot re-runs
      # first-boot setup (new SSH host keys, possibly hostname) - unsafe on
      # e.g. FreeIPA. Found 2026-10-01 rotating the Ansible key.
      initialization[0].user_account,
      # Same reason: a new default snippet must not touch running VMs (2026-10-20).
      initialization[0].user_data_file_id,
      # And the meta-data file (2026-11-04): only new VMs get it at creation.
      initialization[0].meta_data_file_id,
    ]
  }
}
