"""GPU scheduler for in-process GPU users (Whisper, presence, local vision): one queue, one worker, priorities, timeouts.

llama-server processes are serialised by the playground; this covers the work that runs inside the Core process, so STT, presence and screen
work can no longer hit CUDA at the same time (the old "CUDA collisions").

Sockets
  in : run(owner, priority, fn, timeout=None) -> fn's return value; blocks the caller. Lower priority number runs first, FIFO within a number.
       Priorities: INTERACTIVE (a turn is waiting), SENSE (wake/presence/screen), BACKGROUND (consolidation, indexing).
  out: raises GpuTimeout when the job did not finish in `timeout` seconds from the call (queue wait included); fn's own exception is re-raised unchanged.
       stats() -> {queued, running, done, timeouts, by_owner: {owner: {runs, busy_s}}}
A job that times out while still queued is dropped. A job already running cannot be interrupted (CUDA offers no safe kill): the caller gets GpuTimeout,
the worker finishes it and moves on, so one slow job delays but never deadlocks the queue.
Self-check: python gpu.py
"""
import heapq
import itertools
import threading
import time
from collections import defaultdict

import config

INTERACTIVE, SENSE, BACKGROUND = 0, 5, 9


class GpuTimeout(TimeoutError):
    pass


class _Job:
    def __init__(self, owner, fn):
        self.owner, self.fn, self.done, self.cancelled = owner, fn, threading.Event(), False
        self.result = self.error = None


class Scheduler:
    def __init__(self):
        self._q: list = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._running: str | None = None
        self._stats = {"done": 0, "timeouts": 0}
        self._owners = defaultdict(lambda: {"runs": 0, "busy_s": 0.0})
        threading.Thread(target=self._worker, name="gpu-worker", daemon=True).start()

    def _worker(self):
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait()
                _, _, job = heapq.heappop(self._q)
                if job.cancelled:
                    continue
                self._running = job.owner
            t = time.monotonic()
            try:
                job.result = job.fn()
            except BaseException as ex:   # delivered to the caller, never kills the worker
                job.error = ex
            with self._cv:
                o = self._owners[job.owner]
                o["runs"] += 1
                o["busy_s"] += time.monotonic() - t
                self._stats["done"] += 1
                self._running = None
            job.done.set()

    def run(self, owner: str, priority: int, fn, timeout: float | None = None):
        timeout = config.get().gpu_default_timeout_s if timeout is None else timeout
        job = _Job(owner, fn)
        with self._cv:
            heapq.heappush(self._q, (priority, next(self._seq), job))
            self._cv.notify()
        if not job.done.wait(timeout):
            with self._cv:
                job.cancelled = True
                self._stats["timeouts"] += 1
            raise GpuTimeout(f"{owner}: no GPU result in {timeout}s")
        if job.error is not None:
            raise job.error
        return job.result

    def stats(self) -> dict:
        with self._cv:
            return {"queued": sum(1 for *_, j in self._q if not j.cancelled), "running": self._running, **self._stats,
                    "by_owner": {k: dict(v) for k, v in self._owners.items()}}


_default: Scheduler | None = None


def scheduler() -> Scheduler:
    global _default
    if _default is None:
        _default = Scheduler()
    return _default


def run(owner: str, priority: int, fn, timeout: float | None = None):
    return scheduler().run(owner, priority, fn, timeout)


def stats() -> dict:
    return scheduler().stats()


def _selfcheck():
    s, order, active, peak = Scheduler(), [], [0], [0]
    gate = threading.Event()

    def job(tag, wait=None):
        def f():
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            if wait:
                wait.wait(2)
            order.append(tag)
            active[0] -= 1
            return tag
        return f

    ts = [threading.Thread(target=s.run, args=("blocker", SENSE, job("blocker", gate), 5))]
    ts[0].start()
    time.sleep(0.1)   # worker is now busy; the next three queue up
    for tag, pr in (("bg", BACKGROUND), ("sense", SENSE), ("turn", INTERACTIVE)):
        t = threading.Thread(target=s.run, args=(tag, pr, job(tag), 5))
        t.start()
        ts.append(t)
        time.sleep(0.05)
    gate.set()
    for t in ts:
        t.join()
    assert order == ["blocker", "turn", "sense", "bg"], order   # priority, not arrival
    assert peak[0] == 1                                          # never two at once
    assert s.run("x", SENSE, lambda: 42) == 42
    try:
        s.run("bad", SENSE, lambda: 1 / 0)
        raise AssertionError
    except ZeroDivisionError:
        pass
    slow = threading.Event()
    try:
        s.run("slow", SENSE, lambda: slow.wait(1), timeout=0.1)
        raise AssertionError
    except GpuTimeout:
        pass
    assert s.run("after", SENSE, lambda: "ok", timeout=3) == "ok"   # queue survives a timed-out running job
    st = s.stats()
    assert st["timeouts"] == 1 and st["by_owner"]["turn"]["runs"] == 1
    print("gpu ok")


if __name__ == "__main__":
    _selfcheck()
