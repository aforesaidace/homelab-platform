#!/usr/bin/env python3
"""Summarise a `tofu show -json` plan, fingerprint it, and hand it to n8n.

Used by the Jenkins `tofu-plan-apply` pipeline (Jenkinsfile at the repo
root) inside its Kubernetes agent pod. Standard library only, so it runs
in a plain python:3-alpine container.

    python3 scripts/tofu_plan_report.py plan.json --title "..." [--url URL]
    python3 scripts/tofu_plan_report.py plan.json --fingerprint
    python3 scripts/tofu_plan_report.py plan.json --post URL --event plan \
        --kind push --commit SHA [--target T] [--only-if-changes]
    python3 scripts/tofu_plan_report.py - --post URL --event result \
        --kind approved --commit SHA --status applied|mismatch|failed

The fingerprint is sha256 over the commit, the target and the sorted list
of "action address" pairs. n8n's "Tofu Plan Approval" workflow posts the
summary to Discord with an Approve link; an approved apply run re-plans and
applies only if its fingerprint matches the approved one.

The summary says in plain words what happens to each resource: what it is
(VM/container name, node), why a replace happens (tainted after a failed
apply, or which setting can't be changed in place), and for in-place
changes which settings change, old -> new. Values are shown only when tofu
doesn't mark them sensitive, they are short scalars, and the setting name
doesn't look like a credential; everything else is just "changed".
Posting never fails the pipeline (exit 0).
"""
import argparse
import re
import hashlib
import json
import sys
import urllib.request

ICON = {"create": "+", "update": "~", "delete": "-", "replace": "-/+"}


def classify(actions):
    if actions in (["no-op"], ["read"]):
        return None
    if "delete" in actions and "create" in actions:
        return "replace"
    return actions[0]


def summarise(plan):
    changes = {"create": [], "update": [], "delete": [], "replace": []}
    for rc in plan.get("resource_changes", []):
        kind = classify(rc["change"]["actions"])
        if kind in changes:
            changes[kind].append(rc["address"])
    return changes


def fingerprint(changes, commit, target):
    pairs = sorted(f"{k} {a}" for k, v in changes.items() for a in v)
    raw = "\n".join([commit or "", (target or "").strip()] + pairs)
    return hashlib.sha256(raw.encode()).hexdigest()


KIND_WORDS = {
    "proxmox_virtual_environment_vm": "VM",
    "proxmox_virtual_environment_container": "container",
    "cloudflare_dns_record": "DNS record",
    "proxmox_storage_nfs": "NFS storage",
}
SECRETISH = re.compile(r"pass|secret|token|key|private|cert", re.I)
REASONS = {
    "replace_because_tainted": "a previous apply failed while creating it (tofu marked it tainted), so it is rebuilt from scratch",
    "replace_by_request": "a replace was requested (-replace)",
}


def describe(rc):
    """'container test-lxc on pve1' style label from the planned values."""
    # Planned values, falling back to current ones for anything not known yet
    # (a replaced VM's new id) or gone (a destroyed resource).
    after = {**(rc["change"].get("before") or {}),
             **{k: v for k, v in (rc["change"].get("after") or {}).items() if v is not None}}
    what = KIND_WORDS.get(rc["type"], rc["type"])
    name = after.get("name")
    for init in after.get("initialization") or []:
        name = name or init.get("hostname")
    label = f"{what} {name}" if name else what
    if after.get("node_name"):
        label += f" on {after['node_name']}"
    if after.get("vm_id"):
        label += f" (id {after['vm_id']})"
    return label


def short_addr(addr):
    m = re.match(r"module\.([\w-]+)\.", addr)
    return m.group(1) if m else addr


def leaf_diffs(before, after, unknown, sens_b, sens_a, path=""):
    """Yield (path, old, new) for every leaf setting that changes."""
    if isinstance(before, dict) or isinstance(after, dict):
        b, a = before or {}, after or {}
        u = unknown if isinstance(unknown, dict) else {}
        sb = sens_b if isinstance(sens_b, dict) else {}
        sa = sens_a if isinstance(sens_a, dict) else {}
        for k in sorted(set(b) | set(a) | set(u)):
            yield from leaf_diffs(b.get(k), a.get(k), u.get(k), sb.get(k), sa.get(k), f"{path}.{k}" if path else k)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after) == 1:
        yield from leaf_diffs(before[0], after[0],
                              unknown[0] if isinstance(unknown, list) and unknown else None,
                              sens_b[0] if isinstance(sens_b, list) and sens_b else None,
                              sens_a[0] if isinstance(sens_a, list) and sens_a else None, path)
        return
    if unknown is True:
        yield path, before, "(known after apply)"
    elif before != after:
        hidden = sens_b or sens_a or SECRETISH.search(path)
        yield path, None if hidden else before, None if hidden else after


def fmt(v):
    if v is None or v == "":
        return "none"
    if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 40:
        return str(v)
    return None


def change_lines(rc, limit=6):
    c = rc["change"]
    diffs = [d for d in leaf_diffs(c.get("before"), c.get("after"), c.get("after_unknown"),
                                   c.get("before_sensitive"), c.get("after_sensitive"))
             if d[2] != "(known after apply)"]
    out = []
    for path, old, new in diffs[:limit]:
        o, n = fmt(old), fmt(new)
        out.append(f"`{path}` {o} -> {n}" if o is not None and n is not None else f"`{path}` changed")
    if len(diffs) > limit:
        out.append(f"and {len(diffs) - limit} more settings")
    return out


def replace_reason(rc):
    reason = rc.get("action_reason", "")
    if reason in REASONS:
        return REASONS[reason]
    paths = [".".join(str(x) for x in p) for p in rc["change"].get("replace_paths") or []]
    if paths:
        return "these settings can't be changed on a running resource: " + ", ".join(f"`{p}`" for p in paths[:4])
    return "tofu can't change it in place"


def render(title, changes, url, plan=None):
    counts = {k: len(v) for k, v in changes.items()}
    total = sum(counts.values())
    destructive = counts["delete"] + counts["replace"]
    if total == 0:
        lines = [f"**{title}**: no changes. Infrastructure matches the repo."]
        if url:
            lines.append(url)
        return "\n".join(lines), counts, total, destructive
    words = {"replace": "to replace (destroy, then build new)", "delete": "to destroy",
             "create": "to create", "update": "to change in place"}
    parts = [f"{counts[k]} {words[k]}" for k in ("replace", "delete", "create", "update") if counts[k]]
    head = f"**{title}**: " + ", ".join(parts)
    if destructive:
        head = ":rotating_light: " + head + "\n**Destroys something: anything stored on it is lost. Review before approving.**"
    lines = [head]
    by_addr = {rc["address"]: rc for rc in (plan or {}).get("resource_changes", [])}
    sections = [
        ("replace", "**Replace** (old one destroyed, new one built):"),
        ("delete", "**Destroy:**"),
        ("create", "**Create:**"),
        ("update", "**Change in place** (no rebuild):"),
    ]
    shown = 0
    for kind, heading in sections:
        if not changes[kind]:
            continue
        lines.append(heading)
        for addr in changes[kind]:
            if shown >= 15:
                break
            shown += 1
            rc = by_addr.get(addr)
            if not rc:
                lines.append(f"- `{addr}`")
                continue
            item = f"- `{short_addr(addr)}`: {describe(rc)}"
            if kind == "replace":
                item += f". Why: {replace_reason(rc)}"
            lines.append(item)
            if kind == "update":
                lines += [f"  - {l}" for l in change_lines(rc)]
    if total > shown:
        lines.append(f"... and {total - shown} more, see the build log")
    if url:
        lines.append(url)
    return "\n".join(lines), counts, total, destructive


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "User-Agent": "homelab-tofu-report"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:  # reporting must never fail the build
        print(f"(post to n8n failed: {type(e).__name__}: {e})", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan_json", help="tofu show -json output, or - for none (result events)")
    ap.add_argument("--title", default="tofu plan")
    ap.add_argument("--url", default="", help="Jenkins build URL")
    ap.add_argument("--fingerprint", action="store_true", help="print only the fingerprint")
    ap.add_argument("--post", default="", help="n8n webhook URL")
    ap.add_argument("--event", default="plan", choices=["plan", "result"])
    ap.add_argument("--kind", default="manual")
    ap.add_argument("--commit", default="")
    ap.add_argument("--target", default="")
    ap.add_argument("--status", default="")
    ap.add_argument("--only-if-changes", action="store_true")
    a = ap.parse_args()

    changes = {"create": [], "update": [], "delete": [], "replace": []}
    plan = None
    if a.plan_json != "-":
        with open(a.plan_json) as f:
            plan = json.load(f)
        changes = summarise(plan)
    fp = fingerprint(changes, a.commit, a.target)
    if a.fingerprint:
        print(fp)
        return
    text, counts, total, destructive = render(a.title, changes, a.url, plan)
    if not a.post:
        print(text)
        return
    if a.event == "plan" and a.only_if_changes and total == 0:
        print("(no changes - not posting)")
        return
    payload = {
        "event": a.event, "kind": a.kind, "title": a.title, "summary": text,
        "has_changes": total > 0, "destructive": destructive > 0, "counts": counts,
        "fingerprint": fp, "commit": a.commit, "target": a.target,
        "build_url": a.url, "status": a.status,
    }
    post(a.post, payload)
    print(f"(posted {a.event} to n8n)")


if __name__ == "__main__":
    main()
