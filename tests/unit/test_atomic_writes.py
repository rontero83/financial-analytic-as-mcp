"""OPS-05 — atomic-write race test (100 iterations, zero partial reads).

While a producer atomically writes 100 successive JSON payloads to a single
``status.json``-shaped file, a concurrent ``threading.Thread`` reader tight-
loops on ``read_bytes()`` + ``json.loads``. The contract this test enforces
is the spec's atomic-write invariant: every read MUST yield fully parseable
JSON — no partial reads, no truncated mid-flight payloads, no
``json.JSONDecodeError``.

Why ``threading.Thread`` (not asyncio)? The atomic-write helpers in
``task_store`` route through ``anyio.to_thread.run_sync`` in production; the
race we care about is at the **filesystem layer** (rename atomicity, fsync
ordering) — that's an OS-level concern that's only exercised by a real
preempting reader. A coroutine reader would never preempt the producer
between fdopen/replace; an OS thread does.

Reference: 01-04-PLAN.md Task 1; 01-RESEARCH.md §Atomic-Write Race Test.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from finance_skills_mcp.task_store import atomic_write_json


@pytest.mark.parametrize("iteration", range(1))
def test_atomic_write_no_partial_reads(tmp_path: Path, iteration: int) -> None:
    """100 atomic writes + concurrent reader → zero partial-JSON reads."""
    target = tmp_path / "status.json"
    # Seed the file so the reader never observes an absent file on first tick.
    atomic_write_json(target, {"i": -1, "seed": True})

    parse_errors: list[tuple[int, str, str]] = []
    successful_reads = [0]
    stop = threading.Event()

    def reader() -> None:
        """Tight-loop the file; record any parse error or unexpected exception."""
        local_idx = 0
        while not stop.is_set():
            try:
                raw = target.read_bytes()
                json.loads(raw)
                successful_reads[0] += 1
            except FileNotFoundError:
                # Acceptable transient: tmp file briefly absent between
                # unlink and os.replace on some filesystems. Not a parse error.
                pass
            except json.JSONDecodeError as exc:
                parse_errors.append((local_idx, "JSONDecodeError", str(exc)))
            except Exception as exc:  # noqa: BLE001 — record all
                parse_errors.append(
                    (local_idx, type(exc).__name__, str(exc))
                )
            local_idx += 1

    t = threading.Thread(target=reader, name="atomic-write-reader", daemon=True)
    t.start()
    try:
        for i in range(100):
            atomic_write_json(
                target,
                {
                    "i": i,
                    "task_id": f"20260519T000000-{i:08x}",
                    "status": "working",
                    "elapsed_seconds": float(i) * 0.001,
                },
            )
            # Tiny yield so the reader thread actually gets to run between
            # writes (without this on a fast machine the writes can starve
            # the reader, defeating the purpose).
            time.sleep(0.001)
    finally:
        stop.set()
        t.join(timeout=2.0)

    assert not parse_errors, (
        f"OPS-05 race violation: {len(parse_errors)} partial/invalid reads "
        f"observed across {successful_reads[0]} successful reads.\n"
        + "\n".join(
            f"  [{i}] {ex_type}: {msg}" for i, ex_type, msg in parse_errors[:10]
        )
    )
    assert successful_reads[0] > 0, (
        "Reader thread never completed a single read — test is not actually "
        "exercising the race; investigate timing or write a more vigorous "
        f"reader. successful_reads={successful_reads[0]}, "
        f"parse_errors={len(parse_errors)}"
    )
