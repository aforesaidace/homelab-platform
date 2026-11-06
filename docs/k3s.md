# K3s cluster

A 10-node K3s cluster:
- six **unprivileged Proxmox LXCs** spread across both hypervisors;
- four Raspberry Pis as arm64 workers.

It's where I practise Kubernetes operations, and it runs the CI agents (the
Jenkins pipeline's pods).

## Topology

| Node | Role | Hypervisor | vCPU / RAM |
|---|---|---|---|
| k3s-cp1 | control plane (cluster-init) | pve1 | 2 / 2 GB |
| k3s-cp2 | control plane | pve2 | 2 / 2 GB |
| k3s-cp3 | control plane | pve2 | 2 / 2 GB |
| k3s-worker1 | worker | pve1 | 4 / 4 GB |
| k3s-worker2 | worker | pve2 | 4 / 4 GB |
| k3s-gpu1 | worker, privileged, GPU-ready, NFS media mount | pve1 | 4 / 8 GB |
| pi1–pi4 | arm64 workers (Pis; some tainted `arm64-only`) | bare metal | — |

- **Three control-plane nodes with embedded etcd** keep quorum through the
  loss of any one node. Control-plane nodes are tainted `NoSchedule`, so
  workloads run only on workers.
- **Spread across hypervisors:** taking pve1 down for maintenance leaves
  two control-plane nodes and the pve2 workers running.
- The six LXCs are declared in [`tofu/k3s.tf`](../tofu/k3s.tf). K3s is
  installed and joined by
  [`ansible/playbooks/k3s.yml`](../ansible/playbooks/k3s.yml). Cluster
  add-ons are installed by
  [`ansible/playbooks/k3s-addons.yml`](../ansible/playbooks/k3s-addons.yml).

## Running K3s in unprivileged LXCs

This is the unusual part. The usual advice is to use VMs or privileged
containers. I chose unprivileged LXCs because they are much lighter than
VMs, and root inside the container isn't root on the host. That meant
working through what kubelet needs from the kernel.

**1. Host-side LXC settings.** Proxmox's API can't set these, so the
Tofu module can't either. Ansible adds them to `/etc/pve/lxc/<id>.conf`
over SSH to the hypervisor
([`k3s-lxc-config.yml`](../ansible/playbooks/k3s-lxc-config.yml)):

| Setting | Why |
|---|---|
| `lxc.cap.drop:` (empty) | Keep `NET_ADMIN`, which Flannel and kube-proxy need for veth pairs and iptables |
| `lxc.apparmor.profile: unconfined` | Allow the mounts that container runtimes perform |
| `lxc.mount.auto: proc:rw sys:rw cgroup:rw` | Kubelet has to manage cgroups and read kernel parameters |
| `lxc.cgroup2.devices.allow: c 10:200 rwm` | `/dev/net/tun` |
| `features: nesting=1` (in Tofu) | Containers inside the container |

**2. Kernel modules** (`overlay`, `br_netfilter` and others) are loaded on
the **hypervisor**, because an LXC shares the host kernel.

**3. Kubelet flags:**
- `feature-gates=KubeletInUserNamespace=true`: kubelet knows it's in a
  user namespace and skips what it can't do there, such as `/dev/kmsg` and
  some sysctls.
- `protect-kernel-defaults=false`: don't fail on kernel parameters that a
  container can't change.

## Networking

| | Choice | Why |
|---|---|---|
| CNI | **Flannel, `host-gw` backend** | All LXC nodes share one L2 VLAN, so plain routes work with no encapsulation overhead. Cilium needs privileges an unprivileged LXC doesn't have, and Calico is unproven there. |
| NetworkPolicy | K3s's embedded kube-router | No separate controller to run |
| LoadBalancer | **MetalLB** (L2 mode) | Built-in `servicelb` disabled; MetalLB hands out IPs from a reserved range on the services VLAN |
| Ingress | ingress-nginx | K3s's bundled Traefik disabled, so the ingress layer is a deliberate choice rather than a default |
| TLS | cert-manager | Let's Encrypt ClusterIssuer (HTTP-01, staging so far) |
| Storage | local-path-provisioner on a shared NFS path | Volumes reach every worker. The reclaim policy is `Retain`. |

**Lesson learned:** the MetalLB pool (`.50-.59`) once overlapped eight IPs
that were later given to new LXCs. An unclaimed pool address doesn't
answer pings and isn't in the repo, so the usual "grep and ping" check
said each IP was free. It surfaced when ArgoCD returned 502s: its
LoadBalancer IP was also answered over ARP by another container. The pool
is now narrowed to `.50` plus `.70-.79`, with ArgoCD pinned to `.70`, and
the new-host checklist names the pool explicitly.

## GitOps

ArgoCD uses the **app-of-apps** pattern. A root Application points at a
directory of Application manifests in a separate Git repo, and sync waves
order the dependencies. Ansible only bootstraps ArgoCD and the root app;
everything after that is a git commit.

## What I'd do differently at work

- Use VMs (or bare metal) for nodes in a real production cluster. LXCs
  share the host kernel, so a kernel upgrade on the hypervisor is an
  upgrade for every node at once, and a kernel-level problem affects them all.
- Schedule etcd snapshots off-cluster to S3-compatible storage. It's
  documented here but not yet automated.
- Use Cilium where nodes can run it, for eBPF networking and Hubble
  observability.
