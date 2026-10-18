#!/usr/bin/env python3
"""Spreads benchmark runs over several machines, into the results directory that run.py would write.

    dispatch.py --host HOST[,slots=N][,lock=FILE][,local-lock=FILE][,cid=N][,dir=DIR]... [--source DIR]...
                [--out DIR]... [--task TASK] [--variant NAME] [--display WxH@S] [--agent HARNESS:MODEL]...
                [--reps N | --rep N...] [--timeout SECONDS] [--no-debrief]

HOST is `local`, this machine, or an SSH destination such as user@machine, which this machine reaches with its
keys. Each run, one agent's repetition, runs `run.py --rep N` on one host in a fresh VM there, and its directory is
copied back. The commits of this checkout and of each --source are shipped to every host: the checkouts must be
clean. Each --source is a build with its own results directory, the --out in the same order; the runs of builds go
to the same host one after the other. See bench/README.md.
"""

import argparse
import collections
import contextlib
import fcntl
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

print_lock = threading.Lock()


def say(*parts):
    with print_lock:
        print(*parts, flush=True)


def ssh_command(ctl):
    """ssh without prompts, sharing one connection per host through a socket in ctl."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
            "-o", "ControlMaster=auto", "-o", f"ControlPath={ctl}/%C", "-o", "ControlPersist=300"]


class Host:
    """A machine that runs jobs: `local` or an SSH destination, with its options."""

    def __init__(self, spec):
        self.target, *options = spec.split(",")
        if not self.target:
            raise ValueError(f"no host in {spec!r}")
        self.slots, self.cid, self.lock, self.local_lock, self.dir = 1, 4242, None, None, ".cache/hyprmasq-bench"
        for option in options:
            key, sep, value = option.partition("=")
            if not sep or not value:
                raise ValueError(f"{option!r} in {spec!r} is not KEY=VALUE")
            if key in ("slots", "cid"):
                setattr(self, key, int(value))
            elif key in ("lock", "local-lock", "dir"):
                setattr(self, key.replace("-", "_"), value)
            else:
                raise ValueError(f"unknown option {key} in {spec!r}: slots, lock, local-lock, cid or dir")
        if self.slots < 1:
            raise ValueError(f"{spec!r} needs at least one slot")
        self.local = self.target == "local"
        self.base = None  # the absolute directory on the host, once prepared
        self.prepared = None  # True or False once prepare() ran
        self.failures = 0  # jobs in a row that failed before their run started
        self.dropped = False
        self.mutex = threading.Lock()
        self.preparing = threading.Lock()

    def __str__(self):
        return self.target

    def sh(self, script, ctl, timeout=None):
        """Runs a shell script on the host in its home directory; returns the CompletedProcess, output as bytes."""
        script = f'export PATH="$PATH:{":".join(EXTRA_PATH)}"; cd || exit 1\n{script}'
        command = ["bash", "-c", script] if self.local else [*ssh_command(ctl), self.target, "bash -c " + shlex.quote(script)]
        return subprocess.run(command, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def remote(self, path):
        """A path on the host as git and rsync name it."""
        return path if self.local else f"{self.target}:{path}"


@contextlib.contextmanager
def local_lock(path, queue):
    """Holds a lock file on this machine while a host's VM is up, for a host whose other VM users take it here. It
    waits in line with them, and meanwhile counts as waiting in the queue, so that a stopping dispatch need not wait
    for it."""
    if not path:
        yield
        return
    with open(path, "a") as f:
        with queue.waiting():
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Build:
    """One hyprmasq commit to benchmark and its results directory."""

    def __init__(self, out, config):
        self.out, self.config = out, config
        self.checkout, self.commit = config["path"], config["commit"]


class Job:
    """One run: a build's agent and repetition."""

    def __init__(self, build, harness, model, rep):
        self.build, self.harness, self.model, self.rep = build, harness, model, rep
        self.name = f"{harness}-{model}-{rep}"
        self.tried = []  # the hosts it failed on before its run started
        self.outcome = "not run"

    def __repr__(self):
        return f"{self.name} of {self.build.commit[:10]}"


class Unit:
    """The jobs that one host slot takes at once: an agent's repetition of every build, one after the other, so that
    builds compare on the same machine at nearly the same time. A job that a host failed before its run is a unit of
    its own, which may not go back to that host."""

    def __init__(self, jobs, excluded=()):
        self.jobs, self.excluded = list(jobs), set(excluded)


def plan(builds, agents, reps):
    """The units of the runs that have no summary.json yet. Repetitions go round the agents, as in run.py, and the
    order of the builds alternates from one unit to the next, so that neither always runs first."""
    pending = [set(run.pending_runs(b.out, reps, agents)) for b in builds]
    units = []
    for rep in reps:
        for harness, model in agents:
            jobs = [Job(b, harness, model, rep) for b, p in zip(builds, pending) if (rep, harness, model) in p]
            if not jobs:
                continue
            if len(units) % 2:
                jobs.reverse()
            units.append(Unit(jobs))
    return units


class Queue:
    """The units still to run. A slot waits while units remain that only other hosts may take but jobs still run,
    since a failure there can give it work."""

    def __init__(self, units):
        self.units = collections.deque(units)
        self.cond = threading.Condition()
        self.active = 0
        self.stopping = threading.Event()
        self.waiters = 0  # slots waiting for a local lock

    def take(self, host):
        with self.cond:
            while not self.stopping.is_set() and not host.dropped:
                for unit in self.units:
                    if str(host) not in unit.excluded:
                        self.units.remove(unit)
                        self.active += 1
                        return unit
                if not self.active:
                    return None
                self.cond.wait(5)
            return None

    def finish(self, back=()):
        """A slot finished its unit; `back` are units it gives back, first in line."""
        with self.cond:
            self.active -= 1
            self.units.extendleft(reversed(list(back)))
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
        return
    while (unit := queue.take(host)) is not None:
        back = []
        for i, job in enumerate(unit.jobs):
            if queue.stopping.is_set() or host.dropped:
                back.append(Unit(unit.jobs[i:], unit.excluded))
                break
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
                        host.dropped = True
                        say(f"[{host}] dropped: two jobs in a row failed before their run started")
                if len(job.tried) < 2:
                    say(f"[{host}] {job} failed before its run started; it goes to another host")
                    back.append(Unit([job], job.tried))
            else:
                with host.mutex:
                    host.failures = 0
        queue.finish(back)


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


class Remote:
    """Ships the commits to the hosts and runs jobs there with run.py."""

    def __init__(self, harness, builds, agents, args, ctl, queue):
        self.harness, self.builds, self.agents, self.args, self.ctl = harness, builds, agents, args, ctl
        self.queue, self.stopping = queue, queue.stopping

    def sh(self, host, script, timeout=None):
        result = host.sh(script, self.ctl, timeout)
        if result.returncode:
            text = (result.stdout + result.stderr).decode(errors="replace").strip()
            raise RuntimeError(f"[{host}] failed: {text[-2000:]}")
        return result.stdout.decode(errors="replace")

    def ask(self, host, script):
        """Runs a script on the host until the host answers, whatever the script's status: ssh's own 255 or a timeout
        says nothing about a run there, which may still hold the lock."""
        while True:
            with contextlib.suppress(subprocess.TimeoutExpired):
                result = host.sh(script, self.ctl, timeout=120)
                if host.local or result.returncode != 255:
                    return result
            time.sleep(POLL_SECONDS)

    def git(self, *args):
        ssh = shlex.join(ssh_command(self.ctl))
        subprocess.run(["git", *args], check=True, capture_output=True, env=dict(os.environ, GIT_SSH_COMMAND=ssh))

    def tree(self, host, commit):
        return f"{host.base}/trees/{commit}"

    def driver_dir(self, host, build):
        name = f"{self.harness['commit'][:12]}-{build.commit[:12]}-{self.args.task}-{self.args.variant or ''}-{self.args.display}"
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
                host.dropped = True
            return host.prepared

    def _prepare(self, host):
        harnesses = sorted({h for h, _ in self.agents})
        say(f"[{host}] preparing")
        info = self.sh(host, f"""
            for tool in git nix-build python3 rsync setsid {' '.join(harnesses)}; do
                command -v $tool > /dev/null || {{ echo "$tool is not on PATH ($PATH)"; exit 1; }}
            done
            mkdir -p {shlex.quote(host.dir)} && cd {shlex.quote(host.dir)} && pwd
            cat /proc/sys/kernel/hostname; grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-
            {'; '.join(f'{h} --version' for h in harnesses)}
        """).splitlines()
        host.base = info[0].strip()
        say(f"[{host}] {info[1].strip()}, {info[2].strip()}; " + "; ".join(line.strip() for line in info[3:]))
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
            say(f"[{host}] building the driver of {b.commit[:10]}")
            driver_dir = self.driver_dir(host, b)
            call = (f"run.build_driver({driver_dir!r}, {self.tree(host, b.commit)!r}, {self.args.task!r}, "
                    f"{self.args.variant!r}, {self.args.display!r})")
            self.sh(host, f"""
                mkdir -p {shlex.quote(driver_dir)}
                cd {shlex.quote(self.tree(host, self.harness['commit']))}/bench && python3 -c {shlex.quote('import run; ' + call)}
            """)

    def execute(self, host, slot, job):
        b = job.build
        jobs = f"{host.base}/jobs/{hashlib.sha1(b.out.encode()).hexdigest()[:10]}-{job.name}"
        command = [
            "python3", "bench/run.py", "--task", self.args.task, "--display", self.args.display,
            "--agent", f"{job.harness}:{job.model}", "--rep", str(job.rep), "--timeout", str(self.args.timeout),
            "--out", f"{jobs}/out", "--cid", str(host.cid + slot), "--source", self.tree(host, b.commit),
        ]
        command += ["--variant", self.args.variant] if self.args.variant else []
        command += ["--no-debrief"] if self.args.no_debrief else []
        command += ["--lock", host.lock] if host.lock else []
        q = shlex.quote
        local_run = os.path.join(b.out, job.name)
        say(f"[{host}] {job}: starting" + (f", waiting for {host.local_lock} here" if host.local_lock else ""))
        with local_lock(host.local_lock, self.queue):
            if self.stopping.is_set():
                return "not run"
            shutil.rmtree(local_run, ignore_errors=True)  # a run that failed before, as run.py does
            started = False
            # The run gets a session of its own, so that it outlives a dropped connection; the pid file tells
            # whether it started and still runs, and SIGINT to it ends it as Ctrl-C ends run.py. It is launched once:
            # launched again after a lost connection, it would remove a run that started.
            with contextlib.suppress(subprocess.TimeoutExpired):
                started = host.sh(f"""
                    rm -rf {q(jobs)} && mkdir -p {q(jobs)} && cd {q(self.tree(host, self.harness['commit']))} || exit 1
                    setsid -f sh -c {q(f'echo $$ > {q(jobs)}/pid; exec {shlex.join(command)} > {q(jobs)}/log 2>&1 < /dev/null')}
                    for i in $(seq 300); do test -s {q(jobs)}/pid && exit 0; sleep 0.1; done
                    exit 1
                """, self.ctl, timeout=120).returncode == 0
            # Without an answer, the pid file tells whether it started.
            if not started and self.ask(host, f"test -s {q(jobs)}/pid").returncode:
                say(f"[{host}] {job}: the run did not start")
                return "not started"
            lines = self.follow(host, job, jobs)
        # The run's directory as it is, finished or not, as run.py would leave it here.
        if self.ask(host, f"test -d {q(jobs)}/out/{q(job.name)}").returncode == 0:
            shutil.rmtree(local_run, ignore_errors=True)
            ssh = shlex.join(ssh_command(self.ctl))
            for attempt in range(6):
                copy = subprocess.run(["rsync", "-a", "-e", ssh, host.remote(f"{jobs}/out/{job.name}/"), local_run + "/"],
                                      capture_output=True)
                if copy.returncode == 0:
                    break
                time.sleep(POLL_SECONDS)
            else:
                say(f"[{host}] {job}: could not copy the run back from {jobs}, which stays there: "
                    + copy.stderr.decode(errors="replace").strip())
                return "failed"
        outcome = outcome_of(local_run)
        if outcome == "done":
            with open(os.path.join(local_run, "summary.json")) as f:
                s = json.load(f)
            say(f"[{host}] {job}: {'passed' if s['passed'] else 'FAILED'} in {s['wall_seconds']} s, {s['calls']} calls, "
                f"{s['tool_seconds']} s in tools")
        else:
            say(f"[{host}] {job}: {outcome}; the end of its log:\n" + "\n".join(f"    {line}" for line in lines[-25:]))
        host.sh(f"rm -rf {q(jobs)}", self.ctl)
        return outcome

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


def checkout_state(path, parser, what):
    state = run.source_commit(path)
    if not state["commit"]:
        parser.error(f"{what} {state['path']} is not a Git checkout")
    if state["dirty"]:
        parser.error(f"{what} {state['path']} has changes: the hosts get its commit, so commit or stash them first")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_run_options(parser)
    parser.add_argument("--host", action="append", required=True,
                        help="local or an SSH destination, with ,slots=N ,lock=FILE ,local-lock=FILE ,cid=N ,dir=DIR")
    parser.add_argument("--source", action="append", help="hyprmasq checkout to run in the VM, repeatable: a build each")
    parser.add_argument("--out", action="append", help="results directory, one per --source in the same order")
    args = parser.parse_args()
    agents, reps = run.check_run_options(args, parser)
    try:
        hosts = [Host(spec) for spec in args.host]
    except ValueError as e:
        parser.error(str(e))
    if len({str(h) for h in hosts}) < len(hosts):
        parser.error("a host is given twice: give it slots=N instead")

    harness = checkout_state(None, parser, "this checkout")
    sources = [os.path.abspath(s) for s in args.source] if args.source else [None]
    if args.out and len(args.out) != len(sources):
        parser.error(f"{len(args.out)} --out for {len(sources)} builds: give one per --source, or none")
    builds = []
    for i, source in enumerate(sources):
        config = {**checkout_state(source, parser, "the source"), "task": args.task, "variant": args.variant,
                  "display": args.display}
        suffix = f"-{config['commit'][:10]}" if len(sources) > 1 else ""
        out = os.path.abspath(args.out[i] if args.out else run.default_out(args.task, args.variant, suffix))
        builds.append(Build(out, config))
    if len({b.out for b in builds}) < len(builds):
        parser.error("two builds have the same --out")
    for b in builds:
        os.makedirs(b.out, exist_ok=True)
        run.check_resume(b.out, b.config, parser)
        with open(os.path.join(b.out, "source.json"), "w") as f:
            json.dump(b.config, f, indent=2)

    units = plan(builds, agents, reps)
    say(f"{sum(len(u.jobs) for u in units)} runs on {', '.join(f'{h} ({h.slots} slots)' for h in hosts)}")
    queue = Queue(units)
    with tempfile.TemporaryDirectory(prefix="hyprmasq-dispatch-") as ctl:
        remote = Remote(harness, builds, agents, args, ctl, queue)
        dispatch(hosts, queue, remote.prepare, remote.execute)
        for h in hosts:
            if not h.local:
                subprocess.run([*ssh_command(ctl), "-O", "exit", h.target], capture_output=True)

    counts = collections.Counter(job.outcome for u in units for job in u.jobs)
    say("runs: " + ", ".join(f"{n} {outcome}" for outcome, n in counts.items()))
    for b in builds:
        say(f"\n{b.out} ({b.commit[:10]}):\n" + report.report(b.out))


if __name__ == "__main__":
    main()
