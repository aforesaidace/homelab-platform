# 01: An OpenTofu apply would have destroyed 9 of 13 LXCs

**Status:** caught before apply. No outage.

## What happened

While tidying a batch of uncommitted OpenTofu changes, I ran
`tofu plan -refresh=false` before committing. It showed **9 to add, 9 to
destroy**: DNS, the reverse proxy, the whole monitoring stack, Gitea and
the media stack would all be destroyed and recreated at once. None of the
changes were meant to replace anything.

One of those containers kept every media app's database on its local
disk, and it had no backup job at the time. Applying that plan would have
lost data, not just caused downtime.

## Root cause

Three unrelated attributes each forced replacement in the Proxmox
provider, with no real change to the containers:

1. **Template path:** the LXC template had moved to shared storage. The
   file was identical, but its ID went from `local:vztmpl/...` to
   `templates:vztmpl/...`.
2. **Mount-point path:** the NFS mounts were renamed on the host, from
   `/mnt/nfs/media` to `/mnt/pve/tank-media`.
3. **Initial password:** five LXCs had a creation-time password in state,
   but the module now only sets SSH keys.

All three were create-time details that the provider treats as identity.

## Fix

A `lifecycle { ignore_changes = [...] }` block in the shared
`proxmox_lxc` module for exactly those three fields. I checked it against
the real plan, not just `tofu validate`: before, **9 add / 9 destroy**;
after, **0 add / 9 in-place changes / 0 destroy**. The remaining in-place
changes were genuine corrections (DNS server, tags, swap).

## What changed afterwards

- **Every apply is a saved, reviewed plan,** scoped with `-target`. CI
  later made this the only path: Jenkins plans, a person approves that
  exact plan file, then it's applied (see the [`Jenkinsfile`](../../Jenkinsfile)).
- Applies need an explicit `TARGET`. Running against everything means
  typing `ALL`.
- "Confirm zero destroys" is a written step before any apply.
