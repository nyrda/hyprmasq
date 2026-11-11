#!/usr/bin/env bash
# Runs on a machine of the comparison series that another benchmark also uses, beside the series' runs there. While that
# benchmark runs, it holds /tmp/vm.lock exclusive, so that the series' runs in flight end and none starts until it has been
# quiet for 10 minutes, and /tmp/hyprmasq-bench-guard exists, so that a run that overlapped it runs again
# (series.py other_load). It never touches the other benchmark. touch ~/bench/guard-stop ends it.
LOG=~/bench/hyprmasq-guard.log
# What says the other benchmark runs, set where it is started: a systemd unit's name (GUARD_UNIT) and a pattern of its
# processes (GUARD_PATTERN), and any virtual terminal but the desktop's on tty1, where a session of its own would run.
busy() { { test -n "$GUARD_UNIT" && systemctl is-active -q "$GUARD_UNIT"; } ||
  { test -n "$GUARD_PATTERN" && pgrep -f "$GUARD_PATTERN" > /dev/null; } || test "$(cat /sys/class/tty/tty0/active)" != tty1; }
export -f busy
touch /tmp/vm.lock
echo "$(date -Is) guard started" >> $LOG
while ! test -e ~/bench/guard-stop; do
  if busy; then
    # Runs that see this file while the other benchmark runs are run again (bench/series.py other_load).
    touch /tmp/hyprmasq-bench-guard
    echo "$(date -Is) the other benchmark is running: holding the VM lock, runs in flight end first" >> $LOG
    flock -x /tmp/vm.lock bash -c 'echo "$(date -Is) holding the VM lock: no run of the series starts" >> ~/bench/hyprmasq-guard.log; q=0; while test $q -lt 20; do if busy; then q=0; else q=$((q+1)); fi; sleep 30; done'
    rm -f /tmp/hyprmasq-bench-guard
    echo "$(date -Is) quiet for 10 minutes: released the VM lock" >> $LOG
  fi
  sleep 30
done
rm -f /tmp/hyprmasq-bench-guard
echo "$(date -Is) guard stopped" >> $LOG
