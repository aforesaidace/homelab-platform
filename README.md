# Homelab platform: infrastructure as code

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

A home lab run like a small production platform: two Proxmox hypervisors,
about 70 managed hosts (LXC containers, VMs, a 10-node K3s cluster and some
physical machines) and the services on them, all built from code.

This repo is a **curated, scrubbed excerpt** of the private repo I run it
from. It covers the core platform: provisioning, configuration management,
CI/CD, secrets, monitoring, highly available DNS, PKI and Kubernetes. App
roles are left out, along with my personal workstation and the git history.
Domain names, IP addresses and usernames are placeholders (`example.lab`,
`10.20.<vlan>.<host>`); everything else is the real code.

| | |
|---|---|
| **Provisioning** | OpenTofu with the `bpg/proxmox` provider, reusable LXC and VM modules, state in PostgreSQL |
| **Configuration** | Ansible: one role per service and a shared baseline on every host |
| **Secrets** | OpenBao (Vault fork) with a least-privilege AppRole per service. No secrets in git. |
| **CI/CD** | Lint and validate on every push; Jenkins on K3s runs plan → human approval → apply for OpenTofu |
| **Kubernetes** | K3s in unprivileged LXCs across both hypervisors, 3-node embedded-etcd control plane, ArgoCD |
| **Observability** | Prometheus, Alertmanager, Grafana, Loki and Alloy; alerts reach phones via ntfy |
| **Platform** | AdGuard pair behind a keepalived VIP, internal step-ca CA, Traefik, Authentik SSO |

## Where to look first

| If you want to see... | Look at |
|---|---|
| The big picture | [`docs/architecture.md`](docs/architecture.md) |
| How infrastructure is declared | [`tofu/k3s.tf`](tofu/k3s.tf), [`tofu/modules/proxmox_lxc`](tofu/modules/proxmox_lxc/main.tf) |
| The deploy pipeline with an approval gate | [`Jenkinsfile`](Jenkinsfile) |
| Kubernetes on LXC, and why it's built that way | [`docs/k3s.md`](docs/k3s.md), [`ansible/roles/k3s`](ansible/roles/k3s), [`ansible/playbooks/k3s.yml`](ansible/playbooks/k3s.yml) |
| Highly available DNS | [`ansible/playbooks/keepalived_adguard.yml`](ansible/playbooks/keepalived_adguard.yml), [`ansible/roles/keepalived`](ansible/roles/keepalived) |
| Secrets handling | [`scripts/openbao_setup_policies.py`](scripts/openbao_setup_policies.py), any `vault_kv2_get` lookup in [`ansible/playbooks`](ansible/playbooks) |
| The host baseline | [`ansible/roles/common`](ansible/roles/common), [`ansible/roles/node_exporter`](ansible/roles/node_exporter), [`ansible/roles/alloy`](ansible/roles/alloy) |
| Monitoring and alerting | [`ansible/playbooks/monitoring.yml`](ansible/playbooks/monitoring.yml) and its roles |
| Catching drift | [`scripts/drift_check.py`](scripts/drift_check.py), [`ansible/roles/drift_check`](ansible/roles/drift_check) |
| What went wrong and what I changed | [`docs/postmortems/`](docs/postmortems/) |

## Repository layout

```
tofu/                 OpenTofu: providers, K3s nodes, monitoring and platform LXCs, modules/
ansible/
  roles/              one role per service + baseline roles (common, node_exporter, alloy)
  playbooks/          one playbook per service
  inventory.yml       the core hosts (trimmed from the full fleet)
scripts/              Python tooling: OpenBao policies, drift checks, plan reports, discovery
.github/workflows/    CI for this copy: yamllint, ansible-lint, syntax checks, tofu validate, secret scan
.gitea/workflows/     the original CI, as it runs on the private Gitea
Jenkinsfile           OpenTofu plan / approve / apply pipeline
docs/                 architecture, K3s design, postmortems
```

## Running the checks

```bash
pip install ansible ansible-lint yamllint
ansible-galaxy collection install community.general community.docker community.hashi_vault ansible.posix

yamllint -d relaxed ansible/
ansible-lint ansible/roles/ ansible/playbooks/
for pb in ansible/playbooks/*.yml; do ansible-playbook -i ansible/inventory.yml "$pb" --syntax-check; done
tofu -chdir=tofu init -backend=false && tofu -chdir=tofu validate
```

Nothing here can be applied as-is: the hosts, OpenBao and Proxmox endpoints
are placeholders.

## A note on the comments

Many comments explain *why* something is the way it is, often with the date
and the incident behind it. They sometimes mention files that aren't in this
excerpt, such as `docs/todo.md` or "Known Issue #61", which is my private
incident log. Four of those incidents are written up in
[`docs/postmortems/`](docs/postmortems/).

## How I work on it

- **Plan first, apply second.** Every OpenTofu apply is a reviewed, saved
  plan, scoped with `-target`. Risky Ansible changes get a `--check` run first.
- **The repo is the source of truth.** A fix made by hand gets written back
  into a role or module, so a rebuilt host comes back the same.
- **Least privilege.** Each service's AppRole can read only its own secrets,
  and OpenTofu uses a scoped Proxmox API token, not root.
- **Write down what broke.** Every real incident gets a write-up with its
  root cause, fix and follow-up.
- **AI-assisted, human-approved.** I use AI coding agents (Claude Code) for
  much of the day-to-day work. They work under written guardrails and
  hooks: no secrets in output, plan-before-apply, and destructive actions
  need my confirmation.
