#!/bin/bash
# series.sh OUT "backend rep"...: runs the entries one after another into OUT with $RUNNER from this directory,
# default run_form.py, and the runner options in $EXTRA. Stops at the first runner error.
OUT=$1; shift
mkdir -p "$OUT"
for entry in "$@"; do
  set -- $entry
  [ -f "$OUT/$1-$2/summary.json" ] && continue
  echo "$(date +%s) start $1 $2" >> "$OUT/series.log"
  python3 "$(dirname "$0")/${RUNNER:-run_form.py}" --backend "$1" --rep "$2" --out "$OUT" $EXTRA >> "$OUT/series.log" 2>&1 || { echo "$(date +%s) error $1 $2" >> "$OUT/series.log"; exit 1; }
  echo "$(date +%s) end $1 $2" >> "$OUT/series.log"
done
echo "$(date +%s) done" >> "$OUT/series.log"
