# Integration: Live Tier

Phase 1+ fills this directory with nightly tests that hit the **real** Claude Agent SDK. Tests in this tier carry the `live` marker:

```python
@pytest.mark.live
async def test_real_sdk_round_trip(...): ...
```

This tier is **excluded by default** (`pytest -m "not live"` is the project default). It runs only when `ANTHROPIC_API_KEY` is set in the environment; the `conftest.py` in this directory installs a `pytest_collection_modifyitems` hook (Pattern 3) that auto-skips every `@pytest.mark.live` test when the key is missing.

Run only this tier (key required):

```bash
ANTHROPIC_API_KEY=... uv run pytest tests/integration_live -m live
```

In CI, this tier is scheduled as a nightly workflow, not on every PR.
