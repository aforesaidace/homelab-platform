#!/usr/bin/env python3
"""
trivy_sweep.py — Fleet-wide Trivy vulnerability sweep.

Enumerates every image actually running across `docker_update_targets`
(live state via `docker ps`, not what Ansible role defaults claim should
be running - the whole point is catching real drift too) and runs Trivy
against each distinct image once, deduplicated. Runs from wherever it's
invoked (designed for the Gitea Actions runner, see
.gitea/workflows/trivy-scan.yml) - Trivy pulls each image directly from
its registry, it doesn't need to reach the fleet hosts itself, only the
`docker ps` enumeration step does (over SSH via the `ansible` automation
user, same key as every other fleet-wide Ansible operation).

Deliberately conservative on output (see docs/todo.md's Trivy plan):
prints a full report to stdout for manual review. No alerting wired up
yet - decide once a few real runs have shown what the noise level looks
like before deciding whether Discord is worth it.

Usage:
    python3 scripts/trivy_sweep.py [--severity HIGH,CRITICAL]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile


def get_fleet_images():
    """SSH-enumerate every image actually running via Ansible ad-hoc.

    Uses `docker ps --format json` (not the Go-template `{{.Image}}`
    shorthand) specifically because Ansible's own `-a` argument is
    Jinja-templated - a literal `{{ }}` in the command string gets
    parsed as an Ansible template and errors out, not passed through
    to Docker. JSON output sidesteps that entirely.
    """
    with tempfile.TemporaryDirectory() as tree_dir:
        subprocess.run(
            [
                "ansible", "-i", "ansible/inventory.yml", "docker_update_targets",
                "-m", "ansible.builtin.shell",
                "-a", "docker ps --format json",
                "--become", "-t", tree_dir,
            ],
            capture_output=True, text=True, timeout=120,
        )

        images = set()
        host_images = {}
        unreachable = []

        for fname in sorted(os.listdir(tree_dir)):
            with open(os.path.join(tree_dir, fname)) as f:
                result = json.load(f)

            if not result.get("rc") == 0:
                unreachable.append(fname)
                continue

            host_imgs = []
            for line in result.get("stdout", "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    container = json.loads(line)
                except json.JSONDecodeError:
                    continue
                img = container.get("Image")
                if img:
                    images.add(img)
                    host_imgs.append(img)
            host_images[fname] = host_imgs

    return images, host_images, unreachable


def scan_image(image, severity):
    proc = subprocess.run(
        [
            "trivy", "image",
            "--severity", severity,
            "--exit-code", "0",  # never fail the run itself - this is a report, not a gate
            "--format", "table",
            "--quiet",
            image,
        ],
        capture_output=True, text=True, timeout=300,
    )
    return proc.stdout, proc.stderr, proc.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--severity", default="HIGH,CRITICAL")
    args = parser.parse_args()

    print("=== Enumerating fleet images (docker ps across docker_update_targets) ===")
    images, host_images, unreachable = get_fleet_images()

    if unreachable:
        print(f"WARNING: {len(unreachable)} host(s) unreachable or failed, skipped: {', '.join(unreachable)}")

    print(f"\n{len(images)} distinct image(s) found across {len(host_images)} host(s):")
    for img in sorted(images):
        hosts = [h for h, imgs in host_images.items() if img in imgs]
        print(f"  {img}  ({', '.join(hosts)})")

    if not images:
        print("\nNo images found - nothing to scan.")
        sys.exit(0)

    print(f"\n=== Scanning {len(images)} image(s) with Trivy (severity: {args.severity}) ===\n")

    findings = 0
    for img in sorted(images):
        print(f"--- {img} ---")
        stdout, stderr, rc = scan_image(img, args.severity)
        if stdout.strip():
            print(stdout)
            findings += 1
        else:
            print("  (no findings at this severity, or image unavailable)")
        if stderr.strip():
            print(f"  [stderr] {stderr.strip()}", file=sys.stderr)
        print()

    print(f"=== Sweep complete: {findings}/{len(images)} image(s) with findings at {args.severity} ===")


if __name__ == "__main__":
    main()
