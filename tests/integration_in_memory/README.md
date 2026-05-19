# Integration: In-Memory Tier

Phase 1 fills this directory with FastMCP in-memory `Client` tests that exercise the full server wiring without hitting the live Claude Agent SDK. Tests in this tier use the `in_memory` marker:

```python
@pytest.mark.in_memory
async def test_create_task_happy_path(...): ...
```

Run only this tier:

```bash
uv run pytest tests/integration_in_memory -m in_memory
```

The `MockAgentRunner` seam at `tests/_fixtures/mock_agent_runner.py` is injected into the server in this tier so the SDK boundary is never crossed. Phase 0 ships the empty placeholder so the three-tier layout is committed before any production code exists.
