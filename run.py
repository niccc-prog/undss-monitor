"""Run one monitoring cycle: collect -> analyse -> save -> alert.

    python run.py                    # normal run (last `lookback_hours`)
    python run.py --backfill-days 90 # first run: fill in the last 90 days
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from monitor import alerts, analysis, sources, storage

ROOT = Path(__file__).resolve().parent


def main() -> int:
    p = argparse.ArgumentParser(description="UNDSS sentiment monitor")
    p.add_argument("--backfill-days", type=int, default=0, help="look back this many days (max ~90 for GDELT)")
    p.add_argument("--no-alerts", action="store_true", help="collect and save, but send no alerts")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("monitor")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    end = datetime.now(timezone.utc)
    if args.backfill_days:
        start = end - timedelta(days=min(args.backfill_days, 90))
    else:
        start = end - timedelta(hours=config.get("lookback_hours", 48))
    log.info("Collecting mentions from %s to %s", start.isoformat(), end.isoformat())

    raw, status = sources.collect_all(config, start, end)
    log.info("Collected %d raw mentions: %s", len(raw), status)

    enriched = analysis.enrich(raw, config)
    all_df, new_df = storage.merge_and_save(enriched)
    log.info("%d new mentions saved (total %d)", len(new_df), len(all_df))

    fired = []
    if not args.no_alerts:
        fired = alerts.run_alerts(all_df, new_df, config, backfill=bool(args.backfill_days))

    storage.log_run(
        {
            "time": end.isoformat(),
            "window_start": start.isoformat(),
            "sources": status,
            "collected": len(raw),
            "new": int(len(new_df)),
            "total": int(len(all_df)),
            "sentiment_engine": enriched[0].get("sentiment_engine", "") if enriched else "",
            "alerts": fired,
        }
    )
    # exit with an error only if every enabled source failed (shows red in GitHub)
    enabled = [v for v in status.values() if v != "disabled"]
    return 1 if enabled and all(v.startswith("error") for v in enabled) else 0


if __name__ == "__main__":
    sys.exit(main())
