"""Simple file storage: a CSV of mentions plus small JSON logs. Lives in data/."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MENTIONS = DATA_DIR / "mentions.csv"
RUNS = DATA_DIR / "runs.json"
ALERTS = DATA_DIR / "alerts.json"
ALERT_STATE = DATA_DIR / "alert_state.json"

COLUMNS = [
    "id", "published", "first_seen", "source", "outlet", "outlet_country", "language",
    "title", "url", "sentiment", "sentiment_score", "sentiment_engine",
    "topics", "criticism", "countries", "text",
]


def _norm_title(t: str) -> str:
    return re.sub(r"[^\w]+", " ", str(t).lower()).strip()[:120]


def load_mentions() -> pd.DataFrame:
    if not MENTIONS.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(MENTIONS, dtype=str, keep_default_na=False)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNS]


def merge_and_save(new: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add new mentions; returns (all mentions, only the genuinely new ones)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    old = load_mentions()
    now = datetime.now(timezone.utc).isoformat()

    incoming = pd.DataFrame(new) if new else pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:
        if c not in incoming.columns:
            incoming[c] = ""
    incoming = incoming[COLUMNS].astype(str)
    incoming["first_seen"] = now
    incoming["text"] = incoming["text"].str.slice(0, 600)

    # drop items we already have (same URL) or the same headline seen elsewhere
    known_ids = set(old["id"])
    known_titles = set(old["title"].map(_norm_title))
    incoming["_nt"] = incoming["title"].map(_norm_title)
    incoming = incoming[~incoming["id"].isin(known_ids)]
    incoming = incoming[~(incoming["_nt"].isin(known_titles) & (incoming["_nt"].str.len() > 25))]
    incoming = incoming.drop_duplicates("id").drop_duplicates("_nt").drop(columns="_nt")

    merged = pd.concat([old, incoming], ignore_index=True)
    merged = merged.sort_values("published", ascending=False)
    merged.to_csv(MENTIONS, index=False)
    return merged, incoming


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, obj) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def log_run(entry: dict, keep: int = 200) -> None:
    runs = _read_json(RUNS, [])
    runs.insert(0, entry)
    _write_json(RUNS, runs[:keep])


def log_alert(entry: dict, keep: int = 200) -> None:
    alerts = _read_json(ALERTS, [])
    alerts.insert(0, entry)
    _write_json(ALERTS, alerts[:keep])


def load_alert_state() -> dict:
    return _read_json(ALERT_STATE, {})


def save_alert_state(state: dict) -> None:
    _write_json(ALERT_STATE, state)


def load_runs() -> list:
    return _read_json(RUNS, [])


def load_alerts() -> list:
    return _read_json(ALERTS, [])
