# 02: CI lint jobs could never fail

**Status:** resolved. No outage, but no CI signal for months.

## What happened

While auditing the repo against its own documented guardrails, I found
that both lint jobs in CI ended in `|| true`, and had since CI was first
set up. Every run was green whatever it found.

Run for real, the repo had **37 yamllint errors** and **270 ansible-lint
violations** across 190 files.

## Root cause

`|| true` was added to get a first pipeline passing while the repo still
had existing issues, and it was never removed. A green CI badge looked
the same either way, so nothing showed that the check was disabled.

## Fix

- Fixed all 37 yamllint errors (whitespace and newlines).
- Fixed the real ansible-lint findings:
  - `changed_when` on conditional commands;
  - `set -o pipefail` where `curl` pipes into another command;
  - task naming.
- Added a few `# noqa` comments, each with a reason. Examples: "wait for X"
  tasks that must run inline rather than as handlers, and vendor
  install scripts piped from curl.
- Two whole rule groups were too big to fix in one sitting: FQCN (110
  findings) and var-naming (102). They went into a **pinned baseline**
  (`skip_list` in [`.ansible-lint`](../../.ansible-lint)), so new code is
  held to the rule and the old debt stays visible. Paying it down is a
  tracked cleanup.
- Removed `|| true`. Lint failures now fail the build.

## What changed afterwards

- **A check that can't fail isn't a check.** A new CI step should be shown
  failing once on purpose, so you know it can.
- The agent guidelines for this repo say not to widen the lint
  `skip_list` without a written reason.
- Ansible collections are declared in `requirements.yml`. A related
  finding was that `ansible-lint` and `ansible-playbook` resolved
  collections from different paths.
