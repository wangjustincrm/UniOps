"""Pull entities from QBO, optionally incrementally, and (optionally) cache to JSON.

`since` is a prior watermark (QBO LastUpdatedTime, tz-aware ISO string). The
extract is deliberately decoupled from load: callers can persist the returned
rows to data/qbo/<entity>.json so a load error never forces a re-fetch.
"""
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "qbo"


def build_where(since: str | None) -> str:
    if not since:
        return ""
    return f"MetaData.LastUpdatedTime > '{since}'"


def extract_entity(client, entity: str, since: str | None) -> list[dict]:
    """Return all rows for `entity` (full when since is None, else incremental)."""
    return list(client.query_all(entity, where=build_where(since)))


def cache_json(entity: str, rows: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{entity}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path
