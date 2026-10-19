#!/usr/bin/env bash
# Turn the per-task /usr/bin/time -l files of one run into StepUsage JSON lines.
# File names follow the workflow: split_c1.time is task "SPLIT (c1)", concat.time is "CONCAT".
set -euo pipefail
results=${1:?usage: collect_usage.sh RESULTS_DIR}
for f in "$results"/usage/*.time "$results"/concat.time; do
  base=$(basename "$f" .time)
  if [[ $base == *_* ]]; then
    step=$(echo "${base%%_*}" | tr '[:lower:]' '[:upper:]')
    name="$step (${base#*_})"
  else
    name=$(echo "$base" | tr '[:lower:]' '[:upper:]')
  fi
  pipeprof usage bsd-time "$f" --step "$name"
done
