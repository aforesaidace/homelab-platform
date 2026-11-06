output "ip_address" {
  description = "The static IP address assigned to this LXC"
  value       = proxmox_virtual_environment_container.this.initialization[0].ip_config[0].ipv4[0].address
}

output "container_id" {
  description = "The Proxmox VMID of this LXC"
  value       = proxmox_virtual_environment_container.this.vm_id
}
