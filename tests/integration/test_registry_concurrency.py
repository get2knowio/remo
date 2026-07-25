"""Multiprocess stress test for the registry advisory lock (T029, SC-005).

N worker processes each upsert their own disjoint set of entries into the
shared registry.json concurrently, in a tight loop. If `mutate_registry()`'s
locking (`core/registry.py`, research.md R3) were broken, concurrent
read-modify-write cycles would race and lose updates from other workers.
This test asserts the final registry contains exactly N * M entries (no lost
updates) and that the file is valid, parseable JSON with unique (type, name)
keys throughout -- proving the atomic-write guarantee holds under concurrent
writers, not just single-process correctness.

Uses `multiprocessing` (not threads) to exercise real separate-process file
locking, since `fcntl.flock` semantics matter across processes, not just
across threads in one interpreter. The worker function is module-level (not
a nested/local function) so it can be pickled for the `spawn` start method as
well as the default `fork` start method used on Linux.
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

WORKERS = 6
ITERATIONS = 15


def _worker(remo_home: str, worker_id: int, iterations: int) -> None:
    """Runs in a separate process: upsert this worker's own disjoint entries.

    REMO_HOME must be set *before* importing remo_cli, since core.config
    resolves it at call time via os.environ -- setting it first in the
    worker (rather than relying on fixture-side os.environ mutation
    propagating across the process boundary) keeps this correct regardless
    of multiprocessing start method (fork vs spawn).
    """
    os.environ["REMO_HOME"] = remo_home

    from remo_cli.core.known_hosts import get_known_hosts, save_known_host
    from remo_cli.models.host import KnownHost

    for i in range(iterations):
        save_known_host(
            KnownHost(
                type="ssh",
                name=f"worker{worker_id}-{i}",
                host="1.2.3.4",
                user="remo",
                access_mode="direct",
            )
        )
        # Nice-to-have: a concurrent reader must never observe a torn file.
        # get_known_hosts() only succeeds if registry.json parsed as valid
        # JSON, so simply not raising here is the assertion.
        get_known_hosts()


def test_concurrent_upserts_never_lose_entries(tmp_path, monkeypatch):
    """N processes x M iterations upserting disjoint entries never lose
    entries to a lost-update race, and the file never ends up corrupt."""
    remo_home = str(tmp_path / "remo")
    Path(remo_home).mkdir(parents=True, exist_ok=True)

    ctx = multiprocessing.get_context()
    processes = [
        ctx.Process(target=_worker, args=(remo_home, worker_id, ITERATIONS))
        for worker_id in range(WORKERS)
    ]

    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=60)

    for p in processes:
        assert not p.is_alive(), "worker process did not finish within the timeout"
        assert p.exitcode == 0, f"worker process exited with code {p.exitcode}"

    # Read the final state back in THIS process (fresh REMO_HOME resolution).
    monkeypatch.setenv("REMO_HOME", remo_home)
    from remo_cli.core.known_hosts import get_known_hosts

    hosts = get_known_hosts()
    assert len(hosts) == WORKERS * ITERATIONS

    registry_path = Path(remo_home) / "registry.json"
    doc = json.loads(registry_path.read_text())
    entries = doc["hosts"]
    assert len(entries) == WORKERS * ITERATIONS

    keys = [(e["type"], e["name"]) for e in entries]
    assert len(keys) == len(set(keys)), "duplicate (type, name) entries after concurrent writes"

    expected_names = {f"worker{w}-{i}" for w in range(WORKERS) for i in range(ITERATIONS)}
    actual_names = {e["name"] for e in entries}
    assert actual_names == expected_names
