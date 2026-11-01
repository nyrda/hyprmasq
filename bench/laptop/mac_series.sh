#!/bin/zsh
# mac_series.sh OUT REPS SET...: the Mac's runs of run_mac.py, unattended. Each SET is "TASK TOOL MODEL", such as
# "files cua gpt-6.1-terra" or "inkscape cua claude-sonnet-5-5", whose agent is Codex, or Claude Code for a Claude
# model, at medium effort, or "files claude-cu claude-sonnet-5-5", Claude's computer use in the Claude app, which
# runs in a series of its own, without Cua's. It runs rep 1 of every set, then rep 2 of every set, up to REPS, so that a change of the
# machine or the services over the night spreads over the sets, each into OUT/TASK-TOOL-MODEL. Claude Code bills to
# $ANTHROPIC_API_KEY, or to the key in the file $ANTHROPIC_API_KEY_FILE names.
# A run whose summary exists is skipped, so that the series resumes where it stopped. A runner error is logged and
# the series goes on, but three in a row stop it. caffeinate keeps the display on, as a locked Mac stops the tools.
# Run it from ~/hyprmasq-bench in a terminal of the desktop session, with nobody using the Mac.
OUT=$1 REPS=$2; shift 2
B=${0:A:h:h}  # ~/hyprmasq-bench, where mac_setup.sh copies this script into form/
# Claude's computer use runs with Cua Driver's daemons stopped, whose window over the screen blocks its full control,
# and Cua needs them: a series runs one or the other.
if [[ " $* " == *" claude-cu "* && " $* " == *" cua "* ]]; then
  echo "mac_series.sh: run claude-cu's sets in a series of their own, with Cua Driver's daemons stopped" >&2
  exit 2
fi
mkdir -p $OUT
[[ -n $ANTHROPIC_API_KEY_FILE ]] && export ANTHROPIC_API_KEY=$(<$ANTHROPIC_API_KEY_FILE)
caffeinate -dimsu -w $$ &
errors=0
for rep in $(seq 1 $REPS); do
  for set in "$@"; do
    words=(${=set})
    task=$words[1] tool=$words[2] model=$words[3]
    [[ $model == claude-* ]] && harness=claude || harness=codex
    # Claude's computer use runs in the Claude app, which the runner drives; the other tools' agents it starts itself.
    [[ $tool == claude-cu ]] && via=drive || via=exec
    dir=$OUT/$task-$tool-$model
    [[ -f $dir/$tool-$rep/summary.json ]] && continue
    rm -rf $dir/$tool-$rep
    echo "$(date +%s) start $task $tool $model $rep" >> $OUT/series.log
    if $B/qt-macos/bin/python $B/form/run_mac.py --task $task --tool $tool --via $via --harness $harness \
        --model $model --effort medium --rep $rep --out $dir >> $OUT/series.log 2>&1; then
      errors=0
      echo "$(date +%s) end $task $tool $model $rep" >> $OUT/series.log
    else
      errors=$((errors + 1))
      echo "$(date +%s) error $task $tool $model $rep" >> $OUT/series.log
      (( errors >= 3 )) && { echo "$(date +%s) stopped after three errors in a row" >> $OUT/series.log; exit 1; }
    fi
  done
done
echo "$(date +%s) done" >> $OUT/series.log
