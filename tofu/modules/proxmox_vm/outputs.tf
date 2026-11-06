output "ip_address" {
  description = "The static IP address assigned to this VM"
  value       = proxmox_virtual_environment_vm.this.initialization[0].ip_config[0].ipv4[0].address
}

output "vm_id" {
  description = "The Proxmox VMID"
  value       = proxmox_virtual_environment_vm.this.vm_id
}
