"""Bounded cleanup of agents started in a detached process group."""

import os
import signal
import subprocess


def stop_agent(proc, grace=5):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Descendants can outlive the leader, including while keeping stdout open.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def wait_agent(proc, timeout):
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    finally:
        stop_agent(proc)
