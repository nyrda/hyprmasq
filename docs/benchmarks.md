# Benchmarks

The benchmark measures how well coding agents work through `hyprmasq-mcp`.
Each run gives an agent a task in an offline NixOS VM, such as answering a
mail in Thunderbird or uploading a file to a Forgejo repository in a browser.
The agent's window sits on a hidden workspace while the user's own work stays
on the visible one. In the browser task, the user keeps typing into another
window of the same browser. The agent can use only hyprmasq's MCP tools. The
benchmark grades the result from the VM's state, times every call from both
sides of the MCP connection, and asks the agent afterwards where hyprmasq
slowed it down. [bench/README.md](../bench/README.md) describes the tasks, the
setup and how to run it.

## How the runs are read

A change is judged by what the agents did before and after it, from the MCP
log, the agent's transcript and Hyprland's events in the VM:

- whether the task passed, wall time, number of calls, time spent in tools
  and hyprmasq's own share of it
- extra looks: a `screenshot`, or an `act` without actions, right after a
  result that did not show the agent what it needed
- misses: input whose result shows no change, a wrong target, or a way back
  from a wrong page or entry
- the model's time from a result to its next call.

The agent's debrief points to where to look in the logs. It is no evidence
on its own: an agent knows only the run it had, and cannot compare it with a
run without a feature.

## Results

Results of the 0.1.0 release run follow.
