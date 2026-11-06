# 04: A routine image update OOM-killed n8n

**Status:** resolved. n8n (workflow automation) was down until the memory
was raised.

## What happened

- **The update looked clean.** During a fleet-wide "pull the latest
  images" pass, n8n's container was recreated. `systemctl` and
  `docker ps` both reported it healthy.
- **It died 11 minutes later.** The kernel OOM-killed the container, and
  the container's Docker daemon wedged badly enough that unrelated
  `docker` and `systemctl` commands hung.

## Root cause

- **The LXC was already full.** `free -h` inside it showed 1.0 GiB total,
  1.0 GiB used, 14 MB available and no swap. n8n (Node.js) had been
  running right at the limit for weeks.
- **Recreating it tipped it over.** A fresh process with a new image needs
  more memory at startup than one that's been running a long time.
- **It hadn't happened sooner** only because that container hadn't been
  recreated in a while.

## Fix

- **Live relief:** raised the LXC's memory with `pct set <id> -memory 2048`,
  with no reboot. `docker` responded again immediately.
- **Made permanent:** set the same change in OpenTofu (`memory = 2048`).
  `tofu plan -target=module.n8n` showed one in-place change and zero
  destroys, and I applied it.
- **Verified:** n8n came back after the apply's reboot, with an HTTP 200
  and 1.5 GB free.

## What changed afterwards

- **Size for the stack, not the minimum.** New multi-process services get
  headroom above the vendor's stated minimum. Authentik, built the same
  week, got 4 GB rather than 2.
- **"It started" isn't "it's healthy".** After a recreate, check again a
  few minutes later. Prometheus has a `HostLowMemory` alert, so a
  container running at its ceiling shows up before it falls over.
- **A live fix gets codified the same day.** A `pct set` alone would have
  been reverted by the next OpenTofu apply.
