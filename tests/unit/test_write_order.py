"""EXEC-04 — output.md is durable BEFORE status.json flips to ``completed``.

The invariant under test: ``TaskManager.create`` MUST write ``output.md``
(atomically, non-empty) before it writes the terminal ``status.json``. If a
client polls and observes ``status: completed`` it can read ``output.md``
and find a non-empty payload — never a missing file, never an empty one.

Implementation lives in 01-01's ``src/finance_skills_mcp/task_manager.py``
(see the "EXEC-04 order — sacred" comment around the ``atomic_write_text``
of ``output.md`` immediately preceding the status-flip write). This test
proves that invariant *independently* of the integration tests.

Mechanism (matches plan body):
1. Monkey-patch ``task_store.atomic_write_json`` with a wrapper.
2. On every call where the destination is named ``status.json`` AND the
   payload has ``status == "completed"``, the wrapper asserts:
   - ``output.md`` exists in the same task directory.
   - ``output.md`` has non-zero size.
   It THEN delegates to the real ``atomic_write_json`` so the production
   path proceeds normally.
3. Drive a single ``TaskManager.create`` invocation with a deterministic
   ``MockAgentRunner``-style runner.
4. Assert the sentinel was triggered at least once (proves the code path
   actually reached the terminal write — otherwise the test would pass
   trivially without exercising the invariant).

Mutation proof (recorded out-of-band — do NOT leave the mutation in):
Reversing the write order in ``task_manager.create`` (i.e. flipping the
status BEFORE writing output.md) makes this test fail with the
"EXEC-04 violation: output.md missing" assertion message. Verified
empirically during development of this plan (01-04); restored before
commit.

Reference: 01-04-PLAN.md Task 2; 01-CONTEXT.md D-04 (task dir layout) /
D-11 (timeout); 01-01-SUMMARY.md ``patterns-established`` (EXEC-04
ordering).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from finance_skills_mcp import task_store
from finance_skills_mcp.lock_manager import LockManager
from finance_skills_mcp.skill_catalog import Catalog, Skill
from finance_skills_mcp.task_manager import TaskManager


@pytest.mark.anyio
async def test_output_md_durable_before_status_completed(
    tmp_path: Path, monkeypatch
) -> None:
    """status.json never flips to completed until output.md is durable + non-empty."""
    # --- minimal isolated environment ---
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    repo_root = tmp_path  # any directory works — we don't need the real repo

    # Seed a single fixture skill — TaskManager validates `skills=[...]` against
    # the catalog. Use a path that points at an empty dir we create here so
    # stage_skills_in_workspace can copy successfully.
    fixture_skill_path = tmp_path / "fixture-skill"
    fixture_skill_path.mkdir()
    (fixture_skill_path / "SKILL.md").write_text(
        "---\nname: fixture-write-order\n---\nfixture body\n",
        encoding="utf-8",
    )
    catalog = Catalog(
        skills=(
            Skill(
                id="fixture-write-order",
                name="fixture-write-order",
                description="Write-order test fixture.",
                # Relative to repo_root (= tmp_path) → "fixture-skill"
                path="fixture-skill",
            ),
        )
    )

    lock_mgr = LockManager(tasks_root=tasks_root)

    # Deterministic runner module — TaskManager calls `self.agent_runner.run(...)`.
    async def det_runner(prompt: str, skills, cwd: Path) -> str:
        return "WRITE-ORDER-PROOF: deterministic output body for EXEC-04 test"

    agent_runner_module = SimpleNamespace(run=det_runner)

    # --- monkey-patch task_store.atomic_write_json with a write-order sentinel ---
    real_atomic_write_json = task_store.atomic_write_json
    sentinel_triggered = {"count": 0}

    def sentinel_atomic_write_json(dest: Path, obj: object) -> None:
        dest = Path(dest)
        if (
            dest.name == "status.json"
            and isinstance(obj, dict)
            and obj.get("status") == "completed"
        ):
            task_dir = dest.parent
            output_path = task_dir / "output.md"
            assert output_path.exists(), (
                f"EXEC-04 violation: status.json flipped to 'completed' "
                f"BEFORE output.md exists (task_dir={task_dir}, "
                f"obj={obj!r})"
            )
            assert output_path.stat().st_size > 0, (
                f"EXEC-04 violation: status.json flipped to 'completed' "
                f"BUT output.md is empty (task_dir={task_dir}, "
                f"output_size=0)"
            )
            sentinel_triggered["count"] += 1
        # Delegate to the real implementation either way.
        real_atomic_write_json(dest, obj)

    monkeypatch.setattr(task_store, "atomic_write_json", sentinel_atomic_write_json)

    # --- exercise the production code path ---
    task_mgr = TaskManager(
        catalog=catalog,
        lock_mgr=lock_mgr,
        tasks_root=tasks_root,
        repo_root=repo_root,
        agent_runner_module=agent_runner_module,
        task_store_module=task_store,  # picks up the patched atomic_write_json
    )

    result = await task_mgr.create(
        prompt="write-order test prompt",
        skills=["fixture-write-order"],
    )

    # Sanity: the call succeeded (we'd get an ErrorToolResult on failure).
    assert isinstance(result, dict), (
        f"TaskManager.create returned an error result instead of a dict: "
        f"{result!r}"
    )
    assert "task_id" in result, f"missing task_id in result: {result!r}"

    # The load-bearing assertion: the sentinel actually fired, proving the
    # completed-status code path was reached.
    assert sentinel_triggered["count"] >= 1, (
        "EXEC-04 test did not observe the completed-status write — the "
        "monkey-patch was never triggered with status='completed'. Either "
        "TaskManager.create did not reach the terminal write or the "
        "monkey-patch failed to intercept it. count="
        f"{sentinel_triggered['count']}"
    )

    # Belt-and-braces: confirm the actual on-disk state matches what we
    # asserted-on-the-fly. (If the production path ever wrote output.md
    # *after* status.json the sentinel would already have raised, but the
    # end-state check is a cheap defense-in-depth.)
    task_dir = tasks_root / result["task_id"]
    output_path = task_dir / "output.md"
    status_path = task_dir / "status.json"
    assert output_path.exists(), f"output.md missing post-run: {output_path}"
    assert output_path.stat().st_size > 0, "output.md is empty post-run"
    assert status_path.exists(), f"status.json missing post-run: {status_path}"
    output_body = output_path.read_text(encoding="utf-8")
    assert "WRITE-ORDER-PROOF" in output_body, (
        f"output.md does not contain the deterministic runner's output: "
        f"{output_body!r}"
    )
