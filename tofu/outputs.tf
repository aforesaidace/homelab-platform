# ── K3s Cluster ─────────────────────────────────────────────────────────────────
output "k3s_cp1_ip" {
  description = "K3s control plane 1 IP address"
  value       = module.k3s_cp1.ip_address
}

output "k3s_cp2_ip" {
  description = "K3s control plane 2 IP address"
  value       = module.k3s_cp2.ip_address
}

output "k3s_cp3_ip" {
  description = "K3s control plane 3 IP address"
  value       = module.k3s_cp3.ip_address
}

output "k3s_worker1_ip" {
  description = "K3s worker 1 IP address"
  value       = module.k3s_worker1.ip_address
}

output "k3s_worker2_ip" {
  description = "K3s worker 2 IP address"
  value       = module.k3s_worker2.ip_address
}

output "k3s_gpu1_ip" {
  description = "K3s GPU worker IP address"
  value       = module.k3s_gpu1.ip_address
}
