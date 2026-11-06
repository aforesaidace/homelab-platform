# Bootstrap credentials for OpenTofu's own OpenBao access (tofu-kv AppRole,
# scripts/openbao_setup_policies.py). These two values can't themselves live
# in OpenBao (chicken-and-egg) - real values come from
# tofu/vault_secret_ids.auto.tfvars, gitignored, auto-loaded by Terraform.
variable "tofu_kv_role_id" {
  description = "AppRole role_id for OpenTofu's own OpenBao access (tofu-kv)"
  type        = string
}

variable "tofu_kv_secret_id" {
  description = "AppRole secret_id for OpenTofu's own OpenBao access (tofu-kv)"
  type        = string
  sensitive   = true
}

# proxmox_api_token (tofu-agent@pve, PVEAdmin) + proxmox_pve1_root_password
# (root@pam, for the handful of modules Proxmox hard-walls to root@pam
# regardless of ACL role - see main.tf's root_auth provider alias).
data "vault_kv_secret_v2" "tofu" {
  mount = "secret"
  name  = "tofu"
}
