#!/usr/bin/env python3
"""Turn Semgrep's JSON report into a readable list for the run's page (GitHub's job summary).

    python3 scripts/checker-summary.py semgrep reports/semgrep-scan.json

Every finding is listed (rule, severity, file:line, message), so the checker log (docs/CHECKER_LOG.md)
can be written from the run's page. Missing or empty files are skipped. Standard library only.
"""
import json
import os
import sys


def load(path):
    try:
        if os.path.getsize(path) == 0:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def cell(text, limit=160):
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def semgrep(paths):
    lines = []
    for path in paths:
        report = load(path)
        if report is None:
            continue
        results = report.get("results") or []
        errors = report.get("errors") or []
        lines.append(f"### Semgrep: {os.path.basename(path)}: {len(results)} finding(s), {len(errors)} error(s)\n")
        if results:
            lines.append("| Severity | Rule | Where | Message |")
            lines.append("|---|---|---|---|")
            for r in results:
                extra = r.get("extra") or {}
                where = f"{r.get('path')}:{(r.get('start') or {}).get('line', '?')}"
                lines.append(f"| {cell(extra.get('severity'))} | {cell(r.get('check_id'), 80)} "
                             f"| {cell(where, 80)} | {cell(extra.get('message'))} |")
        for e in errors:
            lines.append(f"- Error: {cell(e.get('type'))}: {cell(e.get('message'), 300)}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "semgrep":
        sys.exit("usage: checker-summary.py semgrep REPORT.json [MORE.json]")
    print(semgrep(sys.argv[2:]))
