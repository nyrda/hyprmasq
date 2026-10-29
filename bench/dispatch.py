#!/usr/bin/env python3
"""Spreads benchmark runs over several machines, into the results directory that run.py would write.

    dispatch.py --host HOST[,slots=N][,cores=N][,lock=FILE][,local-lock=FILE][,cid=N][,dir=DIR][,agents=A+B]
                       [,api-keys]...
                [--source DIR]... [--out DIR]... [--task TASK[:VARIANT]]... [--variant NAME] [--display WxH@S]
                [--agent HARNESS:MODEL[@EFFORT]]... [--reps N | --rep N...] [--timeout SECONDS] [--no-debrief]
                [--balance] [--max-concurrent PATTERN=N[,PATTERN=N]...]

HOST is `local`, this machine, or an SSH destination such as user@machine, which this machine reaches with its
keys. Each run, one agent's repetition, runs `run.py --rep N` on one host in a fresh VM there, and its directory is
copied back. agents= limits a host to the agents that match one of its patterns, HARNESS, HARNESS:MODEL or
HARNESS:MODEL@EFFORT with *, a pattern without @ matching the agent at any effort. The harnesses that use this
machine's API keys, claude-api and pi, run only on `local`, but claude-api also on a host with api-keys, which gets
the key through the run's SSH session. The commits of this checkout and of each --source are shipped to every host:
the checkouts must be clean. Each --source is a build with its own results directory, the --out in the same order;
the runs of builds go to the same host one after the other. Several --task run in one queue, each into its own
directory TASK[-VARIANT] under each --out. cores= runs each slot's VM on that many physical cores of its own.
--balance gives each host an equal share of every task's and agent's runs, and --max-concurrent limits the runs at
once of the agents that match a pattern, over all hosts and tasks. See bench/README.md.
"""

import argparse
import collections
import contextlib
import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time

import report
import run

BENCH = os.path.dirname(os.path.abspath(__file__))
# Directories where tools of a user install often are, which a non-interactive SSH session may lack, such as mise's
# shims; appended, so that the host's own PATH comes first.
EXTRA_PATH = ["$HOME/.local/bin", "$HOME/.local/share/mise/shims", "$HOME/.nix-profile/bin",
              "/nix/var/nix/profiles/default/bin"]
POLL_SECONDS = 10
# The harnesses of run.LOCAL_ONLY whose key the dispatcher passes to a host with api-keys: pi reads its keys from its
# files, which would have to be written there.
SENDS_KEY = ("claude-api",)

print_lock = threading.Lock()


def say(*parts):
    with print_lock:
        print(*parts, flush=True)


def ssh_env():
    """The environment for ssh and the tools that run it: without Anthropic keys, such as the one the user set for
    local claude-api runs, which ssh's SendEnv could pass on."""
    return run.without_api_keys(os.environ)


def ssh_command(ctl):
    """ssh without prompts, sharing one connection per host through a socket in ctl."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
            "-o", "ControlMaster=auto", "-o", f"ControlPath={ctl}/%C", "-o", "ControlPersist=300"]


def agent_matches(pattern, harness, model, effort=None):
    """Whether an agent matches a pattern HARNESS, HARNESS:MODEL or HARNESS:MODEL@EFFORT with *: one without @
    matches the agent at any effort, and one with @ only at the efforts it matches."""
    pattern = pattern if ":" in pattern else f"{pattern}:*"
    return fnmatch.fnmatchcase(run.agent_spec(harness, model, effort) if "@" in pattern else f"{harness}:{model}",
                               pattern)


def agent_of(unit):
    job = unit.jobs[0]  # the jobs of a unit are one agent's
    return job.harness, job.model, job.effort


def share_of(unit):
    """What --balance splits in equal shares: an agent's units of one task."""
    job = unit.jobs[0]  # the jobs of a unit are one task's
    return job.build.task, job.build.variant, *agent_of(unit)


class Host:
    """A machine that runs jobs: `local` or an SSH destination, with its options."""

    def __init__(self, spec):
        self.target, *options = spec.split(",")
        if not self.target:
            raise ValueError(f"no host in {spec!r}")
        self.slots, self.cid, self.lock, self.local_lock, self.dir = 1, 4242, None, None, ".cache/hyprmasq-bench"
        self.agents = None  # the patterns of the agents it takes, or None for all
        self.cores = None  # the physical cores of each slot's VM, or None to let the VMs run on any
        self.api_keys = False  # whether it gets this machine's key for the harnesses of SENDS_KEY
        for option in options:
            if option == "api-keys":
                self.api_keys = True
                continue
            key, sep, value = option.partition("=")
            if not sep or not value:
                raise ValueError(f"{option!r} in {spec!r} is not KEY=VALUE")
            if key in ("slots", "cid", "cores"):
                setattr(self, key, int(value))
            elif key in ("lock", "local-lock", "dir"):
                setattr(self, key.replace("-", "_"), value)
            elif key == "agents":
                self.agents = [p if ":" in p else f"{p}:*" for p in value.split("+") if p]
            else:
                raise ValueError(f"unknown option {key} in {spec!r}: slots, cores, lock, local-lock, cid, dir, agents "
                                 "or api-keys")
        if self.slots < 1:
            raise ValueError(f"{spec!r} needs at least one slot")
        if self.cores is not None and self.cores < 1:
            raise ValueError(f"{spec!r} needs at least one core a slot")
        self.local = self.target == "local"
        for harness in {p.split(":", 1)[0] for p in self.agents or []}:
            if not self.local_only_here(harness):
                continue
            if harness in SENDS_KEY:
                raise ValueError(f"{spec!r}: {harness} uses this machine's API key and runs only on local or on a "
                                 "host with api-keys")
            raise ValueError(f"{spec!r}: {harness} uses this machine's key files and runs only on local")
        self.base = None  # the absolute directory on the host, once prepared
        self.cpus = None  # with cores, the CPUs of each slot, once prepared
        self.prepared = None  # True or False once prepare() ran
        self.failures = 0  # jobs in a row that failed before their run started
        self.dropped = False
        self.mutex = threading.Lock()
        self.preparing = threading.Lock()

    def __str__(self):
        return self.target

    def local_only_here(self, harness):
        """Whether the harness uses this machine's API keys, which this host does not get."""
        return harness in run.LOCAL_ONLY and not self.local and not (self.api_keys and harness in SENDS_KEY)

    def takes(self, harness, model, effort=None):
        """Whether the host runs this agent: never a harness that uses this machine's API keys on another machine,
        but for claude-api on one with api-keys. A pattern with @ matches the agent with its effort, and one without
        the agent at any effort."""
        if self.local_only_here(harness):
            return False
        return self.agents is None or any(agent_matches(p, harness, model, effort) for p in self.agents)

    def sh(self, script, ctl, timeout=None, input=None):
        """Runs a shell script on the host in its home directory, with input, bytes, on its stdin; returns the
        CompletedProcess, output as bytes."""
        script = f'export PATH="$PATH:{":".join(EXTRA_PATH)}"; cd || exit 1\n{script}'
        command = ["bash", "-c", script] if self.local else [*ssh_command(ctl), self.target, "bash -c " + shlex.quote(script)]
        stdin = {"stdin": subprocess.DEVNULL} if input is None else {"input": input}
        return subprocess.run(command, capture_output=True, timeout=timeout, env=None if self.local else ssh_env(),
                              **stdin)

    def remote(self, path):
        """A path on the host as git and rsync name it."""
        return path if self.local else f"{self.target}:{path}"


class Build:
    """One hyprmasq commit to benchmark on one task variant, and its results directory."""

    def __init__(self, out, config):
        self.out, self.config = out, config
        self.checkout, self.commit = config["path"], config["commit"]
        self.task, self.variant = config.get("task"), config.get("variant")


class Job:
    """One run: a build's agent and repetition."""

    def __init__(self, build, harness, model, rep, effort=None):
        self.build, self.harness, self.model, self.rep, self.effort = build, harness, model, rep, effort
        self.name = run.run_name(harness, model, rep, effort)
        self.tried = []  # the hosts it failed on before its run started
        self.outcome = "not run"
        self.waited = 0.0  # seconds a --max-concurrent limit held it back from free slots

    def __repr__(self):
        return f"{self.name} of {self.build.commit[:10]}"


class Unit:
    """The jobs that one host slot takes at once: an agent's repetition of every build, one after the other, so that
    builds compare on the same machine at nearly the same time. A job that a host failed before its run is a unit of
    its own, which may not go back to that host."""

    def __init__(self, jobs, excluded=()):
        self.jobs, self.excluded = list(jobs), set(excluded)
        self.blocked = None  # when a full --max-concurrent limit first held it back from a slot
        self.waited = 0.0


def plan(builds, agents, reps):
    """The units of the runs that have no summary.json yet. Repetitions go round the tasks, and each task's round
    round the agents, as in run.py: so an agent's runs, and a provider's, spread over the whole dispatch. The order of
    the builds alternates from one unit to the next, so that neither always runs first."""
    pending = [set(run.pending_runs(b.out, reps, agents)) for b in builds]
    tasks = list(dict.fromkeys((b.task, b.variant) for b in builds))
    units = []
    for rep in reps:
        for task in tasks:
            for harness, model, effort in agents:
                jobs = [Job(b, harness, model, rep, effort) for b, p in zip(builds, pending)
                        if (b.task, b.variant) == task and (rep, harness, model, effort) in p]
                if not jobs:
                    continue
                if len(units) % 2:
                    jobs.reverse()
                units.append(Unit(jobs))
    return units


class Queue:
    """The units still to run. A slot waits while units remain that only other hosts may take but jobs still run,
    since a failure there can give it work.

    With balance, a host takes an agent's unit of a task only within its share of that agent's units of the task,
    which are split equally over the live hosts that take the agent, the remainder to the first that come for it: so
    a faster host does not run more of one agent's runs, whose times depend on the machine. A unit that failed
    elsewhere before its run goes
    to any host. limits are (pattern, n): at most n units of the agents that match the pattern run at once."""

    def __init__(self, units, hosts=(), balance=False, limits=()):
        self.units = collections.deque(units)
        self.cond = threading.Condition()
        self.active = 0
        self.stopping = threading.Event()
        self.waiters = 0  # slots waiting for a local lock
        self.hosts, self.balance, self.limits = list(hosts), balance, list(limits)
        self.running = collections.Counter()  # units running per pattern of limits
        self.planned = collections.Counter(share_of(u) for u in self.units)
        self.taken = collections.defaultdict(collections.Counter)  # per task and agent, the units each host took

    def fair(self, host, unit):
        """Whether the host's share of the unit's task and agent has room for it; units that ran on dropped hosts
        count against no live host's share."""
        if not self.balance or unit.excluded:
            return True
        taken = self.taken[share_of(unit)]
        live = [str(h) for h in self.hosts if not h.dropped and h.takes(*agent_of(unit))]
        left = self.planned[share_of(unit)] - sum(n for h, n in taken.items() if h not in live)
        share, extra = divmod(left, len(live))
        mine = taken[str(host)]
        return mine < share or (mine == share and sum(taken[h] > share for h in live) < extra)

    def full(self, unit):
        """The limits that keep the unit's agent from running another unit now."""
        return [p for p, n in self.limits if agent_matches(p, *agent_of(unit)) and self.running[p] >= n]

    def take(self, host):
        """The next unit for a slot of the host, or None once there is none for it; unit.waited is how long a full
        limit held it back from slots that came for it."""
        with self.cond:
            while not self.stopping.is_set() and not host.dropped:
                held = False
                for unit in self.units:
                    if str(host) in unit.excluded or not host.takes(*agent_of(unit)):
                        continue
                    if not self.fair(host, unit):
                        held = True
                        continue
                    if self.full(unit):
                        unit.blocked = unit.blocked or time.monotonic()
                        continue
                    self.units.remove(unit)
                    self.active += 1
                    if not unit.excluded:  # a unit that failed elsewhere counts for no share
                        self.taken[share_of(unit)][str(host)] += 1
                    for p, n in self.limits:
                        if agent_matches(p, *agent_of(unit)):
                            self.running[p] += 1
                    unit.waited = time.monotonic() - unit.blocked if unit.blocked else 0.0
                    return unit
                # Units held for other hosts' shares wait for those hosts, which take them or are dropped.
                if not self.active and not held:
                    return None
                self.cond.wait(5)
            return None

    def finish(self, host, unit, back=()):
        """A slot of the host finished the unit; `back` are units it gives back, first in line."""
        with self.cond:
            self.active -= 1
            for p, n in self.limits:
                if agent_matches(p, *agent_of(unit)):
                    self.running[p] -= 1
            for u in back:
                if not u.excluded:
                    self.taken[share_of(u)][str(host)] -= 1
            self.units.extendleft(reversed(list(back)))
            self.cond.notify_all()

    def drop(self, host):
        """A host takes no more units: the others' shares grow."""
        with self.cond:
            host.dropped = True
            self.cond.notify_all()

    @contextlib.contextmanager
    def waiting(self):
        with self.cond:
            self.waiters += 1
        try:
            yield
        finally:
            with self.cond:
                self.waiters -= 1

    def stop(self):
        self.stopping.set()
        with self.cond:
            self.cond.notify_all()


def run_slot(host, slot, queue, ready, execute):
    """Takes units for one slot of a host, once ready(host) says it can run them, until there are none for it.
    execute(host, slot, job) runs a job and returns "done", "failed" (it failed after its agent started, and stays as
    run.py leaves it), "not started", or "not run" (the dispatch stopped first)."""
    if not ready(host):
        queue.drop(host)
        return
    while (unit := queue.take(host)) is not None:
        back = []
        for i, job in enumerate(unit.jobs):
            if queue.stopping.is_set() or host.dropped:
                back.append(Unit(unit.jobs[i:], unit.excluded))
                break
            # The unit waited for a limit before its first job; the others follow at once.
            job.waited = unit.waited if i == 0 else 0.0
            try:
                job.outcome = execute(host, slot, job)
            except Exception as e:  # a bug or a local error: the run stays without summary.json, for a resume
                say(f"[{host}] {job}: error: {e!r}")
                job.outcome = "failed"
            if job.outcome == "not run":
                back.append(Unit(unit.jobs[i:], unit.excluded))
                break
            if job.outcome == "not started":
                job.tried.append(str(host))
                with host.mutex:
                    host.failures += 1
                    if host.failures >= 2 and not host.dropped:
                        queue.drop(host)
                        say(f"[{host}] dropped: two jobs in a row failed before their run started")
                if len(job.tried) < 2:
                    say(f"[{host}] {job} failed before its run started; it goes to another host")
                    back.append(Unit([job], job.tried))
            else:
                with host.mutex:
                    host.failures = 0
        queue.finish(host, unit, back)


def dispatch(hosts, queue, ready, execute):
    """Runs the queue's units on the hosts' slots until none is left that a host can take. Ctrl-C stops the queue,
    and the slots then interrupt their runs and wait until these have stopped their VMs."""
    threads = [
        threading.Thread(target=run_slot, args=(host, slot, queue, ready, execute), daemon=True)
        for host in hosts for slot in range(host.slots)
    ]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("stopping: the running runs are interrupted, grade and stop their VMs; Ctrl-C again leaves them")
        queue.stop()
        # Slots still waiting for a local lock have started nothing; the process's end drops them.
        while sum(t.is_alive() for t in threads) > queue.waiters:
            time.sleep(0.5)


def launch_script(jobs, cwd, command, keys=False):
    """The script that starts a run's command in cwd, in a session of its own that outlives the connection, with its
    pid in jobs/pid and its output in jobs/log, and waits until it started. With keys, it first reads claude-api's key
    and request headers from its stdin, each ended by a NUL, and gives them to the run's environment alone: in shell
    variables until then, never in an argument, which ps would show, or a file."""
    q = shlex.quote
    read = (f'IFS= read -r -d "" key && IFS= read -r -d "" headers || exit 1\n'
            f'{run.API_KEY_ENV}="$key" {run.API_HEADERS_ENV}="$headers" ') if keys else ""
    return f"""
rm -rf {q(jobs)} && mkdir -p {q(jobs)} && cd {q(cwd)} || exit 1
{read}setsid -f sh -c {q(f'echo $$ > {q(jobs)}/pid; exec {shlex.join(command)} > {q(jobs)}/log 2>&1 < /dev/null')}
for i in $(seq 300); do test -s {q(jobs)}/pid && exit 0; sleep 0.1; done
exit 1
"""


def key_input(keys):
    """What launch_script reads with keys: the key and the headers, each ended by a NUL."""
    return f"{keys['ANTHROPIC_API_KEY']}\0{keys.get('ANTHROPIC_CUSTOM_HEADERS', '')}\0".encode()


class Remote:
    """Ships the commits to the hosts and runs jobs there with run.py. keys are claude-api's variables, as
    run.anthropic_api_env gives them, or None without a claude-api agent: hosts with api-keys get them, and the files
    of claude-api runs on every host are checked for the key."""

    def __init__(self, harness, builds, agents, args, ctl, queue, keys=None):
        self.harness, self.builds, self.agents, self.args, self.ctl = harness, builds, agents, args, ctl
        self.queue, self.stopping, self.keys = queue, queue.stopping, keys

    def sh(self, host, script, timeout=None):
        result = host.sh(script, self.ctl, timeout)
        if result.returncode:
            text = (result.stdout + result.stderr).decode(errors="replace").strip()
            raise RuntimeError(f"[{host}] failed: {text[-2000:]}")
        return result.stdout.decode(errors="replace")

    def ask(self, host, script, input=None):
        """Runs a script on the host until the host answers, whatever the script's status: ssh's own 255 or a timeout
        says nothing about a run there, which may still hold the lock."""
        while True:
            with contextlib.suppress(subprocess.TimeoutExpired):
                result = host.sh(script, self.ctl, timeout=120, input=input)
                if host.local or result.returncode != 255:
                    return result
            time.sleep(POLL_SECONDS)

    def git(self, *args):
        ssh = shlex.join(ssh_command(self.ctl))
        subprocess.run(["git", *args], check=True, capture_output=True, env=dict(ssh_env(), GIT_SSH_COMMAND=ssh))

    def tree(self, host, commit):
        return f"{host.base}/trees/{commit}"

    def driver_dir(self, host, build):
        name = f"{self.harness['commit'][:12]}-{build.commit[:12]}-{build.task}-{build.variant or ''}-{self.args.display}"
        return f"{host.base}/drivers/{name}"

    def prepare(self, host):
        """Checks the host's tools, ships the commits into a bare repository there with a worktree each, and builds
        the drivers, before the host takes jobs. Returns whether the host can run them."""
        with host.preparing:
            if host.prepared is not None:
                return host.prepared
            host.prepared = False
            try:
                self._prepare(host)
                host.prepared = True
            except (RuntimeError, OSError, subprocess.CalledProcessError) as e:
                detail = e.stderr.decode(errors="replace").strip() if isinstance(e, subprocess.CalledProcessError) else e
                say(f"[{host}] cannot run jobs: {detail}")
            return host.prepared

    def _prepare(self, host):
        harnesses = sorted({run.executable(h) for h, m, e in self.agents if host.takes(h, m, e)})
        say(f"[{host}] preparing")
        info = self.sh(host, f"""
            for tool in git nix-build pkill python3 rsync setsid {' '.join(harnesses)}; do
                command -v $tool > /dev/null || {{ echo "$tool is not on PATH ($PATH)"; exit 1; }}
            done
            mkdir -p {shlex.quote(host.dir)} && cd {shlex.quote(host.dir)} && pwd
            cat /proc/sys/kernel/hostname; grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-
            {'; '.join(f'{h} --version' for h in harnesses)}
        """).splitlines()
        host.base = info[0].strip()
        say(f"[{host}] {info[1].strip()}, {info[2].strip()}; " + "; ".join(line.strip() for line in info[3:]))
        if host.cores:
            siblings = self.sh(host, "cat /sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list")
            try:
                host.cpus = run.slot_cpus(run.physical_cores(siblings.splitlines()), host.slots, host.cores)
            except ValueError as e:
                raise RuntimeError(f"[{host}] {e}") from None
            say(f"[{host}] the slots' VMs run on CPUs " + ", ".join(run.format_cpus(c) for c in host.cpus))
        repo = f"{host.base}/repo.git"
        self.sh(host, f"test -d {shlex.quote(repo)} || git init -q --bare {shlex.quote(repo)}")
        commits = {self.harness["commit"]: self.harness["path"]}
        for b in self.builds:
            commits.setdefault(b.commit, b.checkout)
        for commit, checkout in commits.items():
            self.git("-C", checkout, "push", "-q", host.remote(repo), f"{commit}:refs/bench/{commit}")
            tree = self.tree(host, commit)
            self.sh(host, f"""
                git -C {shlex.quote(repo)} worktree prune
                test -e {shlex.quote(tree)} || git -C {shlex.quote(repo)} worktree add -q --detach {shlex.quote(tree)} {commit}
                test "$(git -C {shlex.quote(tree)} rev-parse HEAD)" = {commit} || {{ echo "{tree} is not at {commit}"; exit 1; }}
                test -z "$(git -C {shlex.quote(tree)} status --porcelain)" || {{ echo "{tree} has changes"; exit 1; }}
            """)
        for b in self.builds:
            say(f"[{host}] building the driver of {b.commit[:10]} for {run.results_name(b.task, b.variant)}")
            driver_dir = self.driver_dir(host, b)
            call = (f"run.build_driver({driver_dir!r}, {self.tree(host, b.commit)!r}, {b.task!r}, "
                    f"{b.variant!r}, {self.args.display!r})")
            self.sh(host, f"""
                mkdir -p {shlex.quote(driver_dir)}
                cd {shlex.quote(self.tree(host, self.harness['commit']))}/bench && python3 -c {shlex.quote('import run; ' + call)}
            """)

    def execute(self, host, slot, job):
        b = job.build
        jobs = f"{host.base}/jobs/{hashlib.sha1(b.out.encode()).hexdigest()[:10]}-{job.name}"
        command = [
            "python3", "bench/run.py", "--task", b.task, "--display", self.args.display,
            "--agent", run.agent_spec(job.harness, job.model, job.effort), "--rep", str(job.rep),
            "--timeout", str(self.args.timeout),
            "--out", f"{jobs}/out", "--cid", str(host.cid + slot), "--source", self.tree(host, b.commit),
        ]
        command += ["--variant", b.variant] if b.variant else []
        command += ["--no-debrief"] if self.args.no_debrief else []
        command += ["--lock", host.lock] if host.lock else []
        command += ["--cpus", run.format_cpus(host.cpus[slot])] if host.cpus else []
        # What run.json records of the dispatch: the slot, the limits on the agent and how long it waited for them.
        limits = {p: n for p, n in self.queue.limits if agent_matches(p, job.harness, job.model, job.effort)}
        command += ["--dispatch", json.dumps({"slot": slot, "balance": self.queue.balance, "max_concurrent": limits,
                                              "waited_seconds": round(job.waited, 1)})]
        q = shlex.quote
        # claude-api's key goes to a host with api-keys on the stdin of the run's start alone; local has its own.
        send = self.keys if job.harness in SENDS_KEY and not host.local else None
        local_run = os.path.join(b.out, job.name)
        say(f"[{host}] {job}: starting" + (f", waiting for {host.local_lock} here" if host.local_lock else ""))
        with run.vm_lock(host.local_lock, self.queue.waiting):
            if self.stopping.is_set():
                return "not run"
            shutil.rmtree(local_run, ignore_errors=True)  # a run that failed before, as run.py does
            started = False
            # The run gets a session of its own, so that it outlives a dropped connection; the pid file tells
            # whether it started and still runs, and SIGINT to it ends it as Ctrl-C ends run.py. It is launched once:
            # launched again after a lost connection, it would remove a run that started.
            with contextlib.suppress(subprocess.TimeoutExpired):
                script = launch_script(jobs, self.tree(host, self.harness["commit"]), command, keys=bool(send))
                started = host.sh(script, self.ctl, timeout=120, input=key_input(send) if send else None).returncode == 0
            # Without an answer, the pid file tells whether it started.
            if not started and self.ask(host, f"test -s {q(jobs)}/pid").returncode:
                say(f"[{host}] {job}: the run did not start")
                return "not started"
            lines = self.follow(host, job, jobs)
        # The run's directory as it is, finished or not, as run.py would leave it here, without files that hold the key.
        leaked = []
        if self.ask(host, f"test -d {q(jobs)}/out/{q(job.name)}").returncode == 0:
            if job.harness in SENDS_KEY and self.keys:
                leaked = self.remove_key(host, job, jobs)
            shutil.rmtree(local_run, ignore_errors=True)
            ssh = shlex.join(ssh_command(self.ctl))
            for attempt in range(6):
                copy = subprocess.run(["rsync", "-a", "-e", ssh, host.remote(f"{jobs}/out/{job.name}/"), local_run + "/"],
                                      capture_output=True, env=ssh_env())
                if copy.returncode == 0:
                    break
                time.sleep(POLL_SECONDS)
            else:
                say(f"[{host}] {job}: could not copy the run back from {jobs}, which stays there: "
                    + copy.stderr.decode(errors="replace").strip())
                return "failed"
        outcome = "failed" if leaked else outcome_of(local_run)  # run.py refused its summary.json
        if outcome == "done":
            with open(os.path.join(local_run, "summary.json")) as f:
                s = json.load(f)
            say(f"[{host}] {job}: {'passed' if s['passed'] else 'FAILED'} in {s['wall_seconds']} s, {s['calls']} calls, "
                f"{s['tool_seconds']} s in tools")
        else:
            say(f"[{host}] {job}: {outcome}; the end of its log:\n" + "\n".join(f"    {line}" for line in lines[-25:]))
        host.sh(f"rm -rf {q(jobs)}", self.ctl)
        return outcome

    def remove_key(self, host, job, jobs):
        """Removes the files of a claude-api run on the host that hold the key, which run.py found too and refused
        the run's summary.json for, so that none is copied back; returns them. grep gets the key on its stdin."""
        result = self.ask(host, f"""
            found=$(grep -rlF -f - -- {shlex.quote(jobs)}); test $? -le 1 || exit 1
            printf '%s\n' "$found" | while IFS= read -r f; do test -z "$f" || rm -f -- "$f"; done
            printf '%s' "$found"
        """, input=(self.keys["ANTHROPIC_API_KEY"] + "\n").encode())
        if result.returncode:
            raise RuntimeError(f"could not check the run's files in {jobs} for the API key, which stay there: "
                               + result.stderr.decode(errors="replace").strip())
        found = [f for f in result.stdout.decode(errors="replace").splitlines() if f]
        if found:
            say(f"[{host}] {job}: the API key was in " + ", ".join(os.path.relpath(f, jobs) for f in found)
                + ": removed there and not copied")
        return found

    def follow(self, host, job, jobs):
        """Prints the run's progress until it ends, and returns its log's lines. A host that stops answering is waited
        for, holding the local lock, since its VM may still be up."""
        q = shlex.quote
        offset, lines, partial, interrupted = 0, [], b"", False
        while True:
            if self.stopping.is_set() and not interrupted:
                with contextlib.suppress(subprocess.SubprocessError, OSError):
                    interrupted = host.sh(f'kill -INT "$(cat {q(jobs)}/pid)"', self.ctl, timeout=60).returncode == 0
            result = self.ask(host, f"""
                if kill -0 "$(cat {q(jobs)}/pid)" 2>/dev/null; then echo running; else echo ended; fi
                tail -c +{offset + 1} {q(jobs)}/log 2>/dev/null || true
            """)
            status, _, new = result.stdout.partition(b"\n")
            offset += len(new)
            *complete, partial = (partial + new).split(b"\n")
            for raw in complete:
                line = raw.decode(errors="replace")
                lines.append(line)
                if line.startswith("["):  # run.py's progress; its report of the one run is the job's, not ours
                    say(f"[{host}] {line}")
            if status == b"ended":
                return lines + ([partial.decode(errors="replace")] if partial else [])
            time.sleep(POLL_SECONDS)


def outcome_of(run_dir):
    """What became of a run, from its directory: done with a summary.json; failed once the agent started, which
    run.py leaves as it is for a later resume to run again; or not started, as when the VM did not boot."""
    if os.path.exists(os.path.join(run_dir, "summary.json")):
        return "done"
    if os.path.exists(os.path.join(run_dir, "agent.jsonl")):
        return "failed"
    return "not started"


def parse_limits(options, agents):
    """The limits of --max-concurrent, (pattern, n) in order: each PATTERN=N, comma-separated, a pattern of agents as
    in agents=. A pattern that matches none of the agents is refused, as a typo would be."""
    limits = {}
    for option in options:
        for item in option.split(","):
            pattern, sep, n = item.partition("=")
            if not sep or not pattern or not n.isdigit() or int(n) < 1:
                raise ValueError(f"--max-concurrent {item!r} is not PATTERN=N with N at least 1")
            if not any(agent_matches(pattern, *agent) for agent in agents):
                raise ValueError(f"--max-concurrent {pattern} matches none of the agents")
            limits[pattern] = int(n)
    return list(limits.items())


def parse_tasks(args, parser):
    """The task variants of --task, each (task, variant): TASK[:VARIANT], or TASK with --variant when it is the only
    one; a task without a variant takes its default, as in run.py."""
    specs = args.task or ["thunderbird"]
    if args.variant and len(specs) > 1:
        parser.error("--variant goes with a single --task: give each --task as TASK:VARIANT")
    tasks = []
    for spec in specs:
        task, sep, variant = spec.partition(":")
        if sep and args.variant:
            parser.error(f"--task {spec} has a variant already: leave out --variant")
        tasks.append((task, run.check_task(task, (variant or None) if sep else args.variant, parser)))
    if len(set(tasks)) < len(tasks):
        parser.error("a task variant is given twice")
    return tasks


def results_out(out, task, variant, tasks, suffix=""):
    """The results directory of a task variant in a dispatch of that many tasks: out itself for one task, as run.py
    takes it, or else a new directory under bench/results; and out/TASK[-VARIANT] for several."""
    if tasks == 1:
        return os.path.abspath(out or run.default_out(task, variant, suffix))
    return os.path.abspath(os.path.join(out, run.results_name(task, variant)))


def checkout_state(path, parser, what):
    state = run.source_commit(path)
    if not state["commit"]:
        parser.error(f"{what} {state['path']} is not a Git checkout")
    if state["dirty"]:
        parser.error(f"{what} {state['path']} has changes: the hosts get its commit, so commit or stash them first")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_run_options(parser, tasks=True)
    parser.add_argument("--host", action="append", required=True,
                        help="local or an SSH destination, with ,slots=N ,cores=N ,lock=FILE ,local-lock=FILE ,cid=N "
                             ",dir=DIR ,agents=PATTERN+PATTERN ,api-keys")
    parser.add_argument("--source", action="append", help="hyprmasq checkout to run in the VM, repeatable: a build each")
    parser.add_argument("--out", action="append",
                        help="results directory, one per --source in the same order; with several --task, the "
                             "directory of their results directories")
    parser.add_argument("--balance", action="store_true",
                        help="give each host an equal share of every agent's runs, instead of to whichever is free")
    parser.add_argument("--max-concurrent", action="append", metavar="PATTERN=N[,PATTERN=N]",
                        help="at most N runs at once, over all hosts, of the agents that match PATTERN, such as "
                             "codex=4,claude-api=3; repeatable")
    args = parser.parse_args()
    agents, reps = run.check_runs(args, parser)
    tasks = parse_tasks(args, parser)
    try:
        limits = parse_limits(args.max_concurrent or [], agents)
    except ValueError as e:
        parser.error(str(e))
    try:
        hosts = [Host(spec) for spec in args.host]
    except ValueError as e:
        parser.error(str(e))
    if len({str(h) for h in hosts}) < len(hosts):
        parser.error("a host is given twice: give it slots=N instead")
    for agent in agents:
        if not any(h.takes(*agent) for h in hosts):
            parser.error(f"no host takes {run.agent_spec(*agent)}"
                         + (": it runs only on local or a host with api-keys" if agent[0] in SENDS_KEY else
                            ": it runs only on local" if agent[0] in run.LOCAL_ONLY else ": see agents="))
    for h in hosts:
        if not any(h.takes(*agent) for agent in agents):
            parser.error(f"host {h} takes none of the agents")

    harness = checkout_state(None, parser, "this checkout")
    sources = [os.path.abspath(s) for s in args.source] if args.source else [None]
    if args.out and len(args.out) != len(sources):
        parser.error(f"{len(args.out)} --out for {len(sources)} builds: give one per --source, or none")
    builds = []
    for i, source in enumerate(sources):
        state = checkout_state(source, parser, "the source")
        suffix = f"-{state['commit'][:10]}" if len(sources) > 1 else ""
        out = args.out[i] if args.out else None
        if not out and len(tasks) > 1:
            out = run.results_dir(f"{len(tasks)}-tasks{suffix}")
        for task, variant in tasks:
            config = {**state, "task": task, "variant": variant, "display": args.display}
            builds.append(Build(results_out(out, task, variant, len(tasks), suffix), config))
    if len({b.out for b in builds}) < len(builds):
        parser.error("two builds have the same --out")
    for b in builds:
        os.makedirs(b.out, exist_ok=True)
        run.check_resume(b.out, b.config, parser, agents)
        with open(os.path.join(b.out, "source.json"), "w") as f:
            json.dump(b.config, f, indent=2)

    # claude-api's key, to check the runs' files for it, and for the hosts with api-keys; the same that run.py here
    # would use.
    keys = None
    if any(harness in SENDS_KEY for harness, model, effort in agents):
        try:
            keys = run.anthropic_api_env()
        except RuntimeError as e:
            parser.error(str(e))

    units = plan(builds, agents, reps)
    say(f"{sum(len(u.jobs) for u in units)} runs" + (f" of {len(tasks)} tasks" if len(tasks) > 1 else "") + " on "
        + ", ".join(f"{h} ({h.slots} slots" + (f" of {h.cores} cores" if h.cores else "") + ")" for h in hosts)
        + (", each agent's runs in equal shares" if args.balance else ""))
    queue = Queue(units, hosts, args.balance, limits)
    with tempfile.TemporaryDirectory(prefix="hyprmasq-dispatch-") as ctl:
        remote = Remote(harness, builds, agents, args, ctl, queue, keys)
        dispatch(hosts, queue, remote.prepare, remote.execute)
        for h in hosts:
            if not h.local:
                subprocess.run([*ssh_command(ctl), "-O", "exit", h.target], capture_output=True, env=ssh_env())

    counts = collections.Counter(job.outcome for u in units for job in u.jobs)
    say("runs: " + ", ".join(f"{n} {outcome}" for outcome, n in counts.items()))
    for b in builds:
        say(f"\n{b.out} ({b.commit[:10]}, {run.results_name(b.task, b.variant)}):\n" + report.report(b.out))


if __name__ == "__main__":
    main()
