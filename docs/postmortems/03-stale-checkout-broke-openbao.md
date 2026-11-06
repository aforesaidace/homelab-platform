# 03: OpenBao crash-looped after an approved update

**Status:** resolved. Secrets service down until it was found; no data loss.

## What happened

OpenBao, the secrets store for the whole fleet, was crash-looping about
once a minute with:

```
Error initializing core: cannot seal migrate from "transit" to Shamir,
no disabled seal in configuration
```

Its rendered config had lost the `seal "transit"` block, which is what lets
it auto-unseal through a second, small OpenBao instance.

## Root cause

- **The trigger:** container updates are approved in Discord, and an n8n
  workflow then runs the matching Ansible playbook. It runs from a
  **second git checkout** of this repo on the n8n host.
- **The fault:** that checkout's copy of the encrypted secrets file was
  weeks out of date. It had no `openbao_seal_token`.
- **Why it slipped through:** the seal block is optional in the template
  (`{% if openbao_seal_token %}`), so the missing value didn't raise an
  error. The block was just left out. OpenBao then refused to start: it
  saw a config asking it to migrate from transit to Shamir unsealing, with
  nothing to migrate to.

Two choices made sense on their own and combined badly:
- a secrets file copied to several machines;
- a template that treats "missing" as "off".

## Fix

- **Immediate:** re-ran the playbook from the main checkout. OpenBao
  started unsealed, and a test read of a real secret confirmed no data
  was lost.
- **Short term:** synced the second checkout and re-ran the exact command
  the workflow builds, to prove it now renders the same config.

## What changed afterwards

- **The copied secrets file was retired.** All secrets now live only in
  OpenBao:
  - each playbook reads its own secrets at run time with its own AppRole;
  - there's no file in git, and no copies to go stale on other machines.
- **Required values should fail loudly.** For a setting that must exist,
  an empty value should stop the run, not quietly drop a config block.
- **Know every place automation runs from.** The hidden second checkout
  was the real surprise here.
