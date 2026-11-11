#!/usr/bin/env bash
# Keeps a machine of the comparison series from sleeping, by idle or by its lid, while the series runs there: logind asks
# root for the lid. touch ~/bench/guard-stop ends it.
exec sudo -n systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block --who=hyprmasq-bench \
  --why="hyprmasq comparison series" sudo -n -u "$USER" bash -c "until test -e $HOME/bench/guard-stop; do sleep 60; done"
