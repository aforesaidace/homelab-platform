# Postmortems

Short, blameless write-ups of real incidents from this lab, taken from my
private incident log (130+ entries). Each covers what happened, why, the
fix, and what changed afterwards so it can't happen the same way again.

| # | Incident | Theme |
|---|---|---|
| [01](01-tofu-blind-apply.md) | An unreviewed OpenTofu apply would have destroyed and recreated 9 of 13 LXCs | Change safety |
| [02](02-ci-lint-never-failed.md) | CI lint jobs couldn't fail: 300+ findings swallowed since day one | Trustworthy CI |
| [03](03-stale-checkout-broke-openbao.md) | OpenBao crash-looped after an approved update; a stale second checkout was the cause | Secrets and automation |
| [04](04-n8n-oom-after-image-update.md) | A routine image update OOM-killed n8n and hung its container | Capacity |
