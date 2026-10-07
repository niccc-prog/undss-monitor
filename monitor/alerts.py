"""Spike detection and alert delivery (email and/or Telegram, both free)."""
from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

import pandas as pd
import requests

from . import storage

log = logging.getLogger("monitor.alerts")


def _published(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["published"], utc=True, errors="coerce", format="ISO8601")


def detect_spike(df: pd.DataFrame, multiplier: float, min_count: int, now: datetime | None = None) -> dict | None:
    """Negative mentions in the last 24h vs the daily average of the 7 days before."""
    if df.empty:
        return None
    now = now or datetime.now(timezone.utc)
    pub = _published(df)
    neg = df[(df["sentiment"] == "negative")].assign(_p=pub)
    last24 = neg[neg["_p"] > now - timedelta(hours=24)]
    prev = neg[(neg["_p"] <= now - timedelta(hours=24)) & (neg["_p"] > now - timedelta(days=8))]
    baseline = len(prev) / 7.0
    count = len(last24)
    if count >= min_count and count >= multiplier * max(baseline, 0.5):
        return {
            "count": count,
            "baseline": round(baseline, 2),
            "ratio": round(count / max(baseline, 0.5), 1),
            "examples": last24.sort_values("_p", ascending=False)
            .head(8)[["title", "url", "outlet", "source"]]
            .to_dict("records"),
        }
    return None


# --------------------------------------------------------------------------
# delivery
# --------------------------------------------------------------------------
def _send_email(subject: str, body_html: str) -> bool:
    to = os.getenv("ALERT_EMAIL_TO")
    user = os.getenv("SMTP_USER")
    pw = os.getenv("SMTP_PASSWORD")
    if not (to and user and pw):
        return False
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(user, pw)
            s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Email alert failed: %s", e)
        return False


def _send_telegram(text_html: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text_html[:4000], "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram alert failed: %s", e)
        return False


def _items_html(items: list[dict]) -> str:
    return "\n".join(
        f'• <a href="{html.escape(i["url"])}">{html.escape(str(i["title"])[:160])}</a> — {html.escape(str(i.get("outlet") or i.get("source", "")))}'
        for i in items
    )


def deliver(kind: str, subject: str, intro: str, items: list[dict]) -> list[str]:
    dashboard = os.getenv("DASHBOARD_URL", "")
    link = f'\n\n<a href="{html.escape(dashboard)}">Open the dashboard</a>' if dashboard else ""
    body = f"<b>{html.escape(subject)}</b>\n{html.escape(intro)}\n\n{_items_html(items)}{link}"
    sent = []
    if _send_telegram(body):
        sent.append("telegram")
    if _send_email(subject, body.replace("\n", "<br>")):
        sent.append("email")
    storage.log_alert(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "subject": subject,
            "intro": intro,
            "items": items,
            "delivered_via": sent,
        }
    )
    if not sent:
        log.info("Alert logged (no email/Telegram configured): %s", subject)
    return sent


def _cooled_down(state: dict, kind: str, hours: float) -> bool:
    last = state.get(kind)
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) > timedelta(hours=hours)


def run_alerts(all_df: pd.DataFrame, new_df: pd.DataFrame, config: dict, backfill: bool = False) -> list[str]:
    cfg = config["alerts"]
    state = storage.load_alert_state()
    fired = []

    spike = detect_spike(all_df, cfg["spike_multiplier"], cfg["spike_min_count"])
    if spike and _cooled_down(state, "spike", cfg["cooldown_hours"]):
        deliver(
            "spike",
            f"⚠️ UNDSS monitor: negative coverage spike ({spike['count']} in 24h)",
            f"{spike['count']} negative mentions in the last 24 hours — "
            f"{spike['ratio']}× the recent daily average of {spike['baseline']}.",
            spike["examples"],
        )
        state["spike"] = datetime.now(timezone.utc).isoformat()
        fired.append("spike")

    if cfg.get("alert_on_new_criticism") and not backfill and not new_df.empty:
        crit = new_df[(new_df["criticism"].astype(str) == "True") & (new_df["sentiment"] == "negative")]
        if not crit.empty:
            items = crit.head(10)[["title", "url", "outlet", "source"]].to_dict("records")
            deliver(
                "criticism",
                f"🔎 UNDSS monitor: {len(crit)} new critical mention(s)",
                "New mentions that appear to criticise UN security arrangements or UNDSS:",
                items,
            )
            fired.append("criticism")

    storage.save_alert_state(state)
    return fired
