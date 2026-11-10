#!/bin/sh
# Print every JSON report in a folder into the run's log, each in its own collapsible group, so the
# full findings can be read even where the kept files can't be
# downloaded. Usage: sh scripts/print-reports.sh reports
dir="${1:-reports}"
[ -d "$dir" ] || exit 0
for f in "$dir"/*.json; do
  [ -f "$f" ] || continue
  echo "::group::CHECKER-REPORT $(basename "$f")"
  cat "$f"
  echo
  echo "::endgroup::"
done
