"""Collectors for each free data source.

Every collector returns a list of "mention" dicts with the same fields:
    id, source, url, title, text, published (ISO UTC), language,
    outlet, outlet_country, gdelt_tone_label (only for GDELT)
Each collector catches its own errors so one failing source never stops a run.
"""
from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

log = logging.getLogger("monitor.sources")

USER_AGENT = "UNDSS-Sentiment-Monitor/1.0 (public-data research prototype)"
TIMEOUT = 30

LANG_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "ar": "Arabic"}
GDELT_LANG_TO_CODE = {v: k for k, v in LANG_NAMES.items()}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _id(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get(url: str, **kw) -> requests.Response:
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    return requests.get(url, headers=headers, timeout=TIMEOUT, **kw)


def _mention(**fields) -> dict:
    base = {
        "id": _id(fields["url"]),
        "source": "",
        "url": "",
        "title": "",
        "text": "",
        "published": "",
        "language": "",
        "outlet": "",
        "outlet_country": "",
        "gdelt_tone_label": "",
    }
    base.update(fields)
    return base


def _check(tries: int, fails: int, last_err: Exception | None) -> None:
    """Raise if every request to a source failed, so the run log shows it as an error."""
    if tries and fails == tries:
        raise RuntimeError(f"all {tries} requests failed ({last_err.__class__.__name__ if last_err else 'unknown'})")


def all_terms(config: dict) -> list[str]:
    seen, out = set(), []
    for terms in config["search_terms"].values():
        for t in terms:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


# --------------------------------------------------------------------------
# GDELT  (https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
# --------------------------------------------------------------------------
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_PAUSE = 6  # GDELT asks for no more than one request every 5 seconds


def _gdelt_query(config: dict) -> str:
    # GDELT searches machine-translated text, so English + acronym terms catch
    # most languages. Only ASCII terms are sent (GDELT rejects some scripts).
    terms = [t for t in all_terms(config) if t.isascii() and len(t) >= 4]
    parts = [f'"{t}"' if " " in t else t for t in terms]
    return "(" + " OR ".join(parts) + ")" if len(parts) > 1 else parts[0]


def _gdelt_artlist(query: str, start: datetime, end: datetime) -> list[dict]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 250,
        "sort": "DateDesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    for attempt in range(3):
        r = _get(GDELT_URL, params=params)
        if r.status_code == 429:
            time.sleep(GDELT_PAUSE * (attempt + 2))
            continue
        r.raise_for_status()
        txt = r.text.strip()
        if not txt or not txt.startswith("{"):
            # GDELT returns plain-text messages for empty results / bad queries
            if txt:
                log.info("GDELT says: %s", txt[:200])
            return []
        return r.json().get("articles", []) or []
    return []


def fetch_gdelt(config: dict, start: datetime, end: datetime) -> list[dict]:
    """Fetch articles and label them with GDELT's own tone score.

    ArtList doesn't return tone, so we run the same query three times:
    all articles, tone below the negative threshold, tone above the positive
    threshold. Anything in neither bucket is neutral.
    """
    s = config["sentiment"]
    base = _gdelt_query(config)
    neg_q = f"{base} tone<{s['gdelt_negative_below']}"
    pos_q = f"{base} tone>{s['gdelt_positive_above']}"

    out: dict[str, dict] = {}
    tries = fails = 0
    last_err = None
    # GDELT caps results at 250 per query, so split long ranges into chunks
    chunk = timedelta(days=3)
    cursor = start
    while cursor < end:
        stop = min(cursor + chunk, end)
        tries += 1
        try:
            arts = _gdelt_artlist(base, cursor, stop)
            time.sleep(GDELT_PAUSE)
            neg = {a["url"] for a in _gdelt_artlist(neg_q, cursor, stop)}
            time.sleep(GDELT_PAUSE)
            pos = {a["url"] for a in _gdelt_artlist(pos_q, cursor, stop)}
            time.sleep(GDELT_PAUSE)
        except Exception as e:  # noqa: BLE001
            fails, last_err = fails + 1, e
            log.warning("GDELT failed for %s–%s: %s", cursor, stop, e)
            cursor = stop
            continue

        for a in arts:
            url = a.get("url")
            if not url:
                continue
            label = "negative" if url in neg else "positive" if url in pos else "neutral"
            try:
                pub = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pub = stop
            out[url] = _mention(
                source="GDELT news",
                url=url,
                title=_clean(a.get("title")),
                text=_clean(a.get("title")),
                published=pub.isoformat(),
                language=GDELT_LANG_TO_CODE.get(a.get("language", ""), (a.get("language") or "")[:2].lower()),
                outlet=a.get("domain", ""),
                outlet_country=a.get("sourcecountry", ""),
                gdelt_tone_label=label,
            )
        cursor = stop
    _check(tries, fails, last_err)
    log.info("GDELT: %d articles", len(out))
    return list(out.values())


# --------------------------------------------------------------------------
# Google News RSS
# --------------------------------------------------------------------------
def fetch_google_news(config: dict, start: datetime, end: datetime) -> list[dict]:
    out: dict[str, dict] = {}
    tries = fails = 0
    last_err = None
    days = max(1, (end - start).days or 1)
    for lang, terms in config["search_terms"].items():
        query = " OR ".join(f'"{t}"' if " " in t else t for t in terms)
        query += f" when:{days}d"
        for region in config.get("google_news_editions", {}).get(lang, ["US"]):
            url = (
                "https://news.google.com/rss/search?q="
                + quote_plus(query)
                + f"&hl={lang}&gl={region}&ceid={region}:{lang}"
            )
            tries += 1
            try:
                r = _get(url)
                r.raise_for_status()
                root = ET.fromstring(r.content)
            except Exception as e:  # noqa: BLE001
                fails, last_err = fails + 1, e
                log.warning("Google News %s-%s failed: %s", lang, region, e)
                continue
            for item in root.iter("item"):
                link = (item.findtext("link") or "").strip()
                if not link:
                    continue
                title = _clean(item.findtext("title"))
                src_el = item.find("source")
                outlet = _clean(src_el.text) if src_el is not None else ""
                # Google appends " - Outlet" to titles; strip it
                if outlet and title.endswith(f" - {outlet}"):
                    title = title[: -len(outlet) - 3]
                try:
                    pub = parsedate_to_datetime(item.findtext("pubDate")).astimezone(timezone.utc)
                except Exception:  # noqa: BLE001
                    pub = end
                if pub < start:
                    continue
                out[link] = _mention(
                    source="Google News",
                    url=link,
                    title=title,
                    text=title + ". " + _clean(item.findtext("description")),
                    published=pub.isoformat(),
                    language=lang,
                    outlet=outlet,
                    outlet_country=region,
                )
            time.sleep(1)
    _check(tries, fails, last_err)
    log.info("Google News: %d articles", len(out))
    return list(out.values())


# --------------------------------------------------------------------------
# Reddit  (public JSON; optional free API keys for reliability)
# --------------------------------------------------------------------------
def _reddit_token() -> str | None:
    cid, secret = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None
    r = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(cid, secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("access_token")


def fetch_reddit(config: dict, start: datetime, end: datetime) -> list[dict]:
    out: dict[str, dict] = {}
    try:
        token = _reddit_token()
    except Exception as e:  # noqa: BLE001
        log.warning("Reddit login failed, using public access: %s", e)
        token = None
    base = "https://oauth.reddit.com/search" if token else "https://www.reddit.com/search.json"
    headers = {"Authorization": f"bearer {token}"} if token else {}
    window = "week" if (end - start) <= timedelta(days=7) else "month" if (end - start) <= timedelta(days=31) else "year"

    tries = fails = 0
    last_err = None
    for term in all_terms(config):
        q = f'"{term}"' if " " in term else term
        tries += 1
        try:
            r = _get(base, params={"q": q, "sort": "new", "limit": 100, "t": window, "type": "link"}, headers=headers)
            r.raise_for_status()
            posts = r.json().get("data", {}).get("children", [])
        except Exception as e:  # noqa: BLE001
            fails, last_err = fails + 1, e
            log.warning("Reddit search '%s' failed: %s", term, e)
            continue
        for p in posts:
            d = p.get("data", {})
            pub = datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc)
            if pub < start:
                continue
            url = "https://www.reddit.com" + d.get("permalink", "")
            title = _clean(d.get("title"))
            out[url] = _mention(
                source="Reddit",
                url=url,
                title=title,
                text=(title + ". " + _clean(d.get("selftext")))[:2000],
                published=pub.isoformat(),
                language="",  # detected later
                outlet="r/" + d.get("subreddit", ""),
            )
        time.sleep(2)
    _check(tries, fails, last_err)
    log.info("Reddit: %d posts", len(out))
    return list(out.values())


# --------------------------------------------------------------------------
# Bluesky
# --------------------------------------------------------------------------
def _bsky_session() -> tuple[str, str] | None:
    handle, pw = os.getenv("BSKY_HANDLE"), os.getenv("BSKY_APP_PASSWORD")
    if not (handle and pw):
        return None
    r = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": pw},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return "https://bsky.social", r.json()["accessJwt"]


def fetch_bluesky(config: dict, start: datetime, end: datetime) -> list[dict]:
    out: dict[str, dict] = {}
    try:
        sess = _bsky_session()
    except Exception as e:  # noqa: BLE001
        log.warning("Bluesky login failed: %s", e)
        sess = None
    host, headers = ("https://public.api.bsky.app", {})
    if sess:
        host, headers = sess[0], {"Authorization": f"Bearer {sess[1]}"}

    tries = fails = 0
    last_err = None
    for term in all_terms(config):
        tries += 1
        params = {
            "q": f'"{term}"' if " " in term else term,
            "limit": 100,
            "sort": "latest",
            "since": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            r = _get(f"{host}/xrpc/app.bsky.feed.searchPosts", params=params, headers=headers)
            if r.status_code in (401, 403) and not sess:
                raise RuntimeError("needs login — add BSKY_HANDLE and BSKY_APP_PASSWORD secrets")
            r.raise_for_status()
            posts = r.json().get("posts", [])
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            fails, last_err = fails + 1, e
            log.warning("Bluesky search '%s' failed: %s", term, e)
            continue
        for p in posts:
            rec = p.get("record", {})
            handle = p.get("author", {}).get("handle", "")
            rkey = p.get("uri", "").rsplit("/", 1)[-1]
            url = f"https://bsky.app/profile/{handle}/post/{rkey}"
            try:
                pub = datetime.fromisoformat(rec.get("createdAt", "").replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pub = end
            if pub < start:
                continue
            text = _clean(rec.get("text"))
            langs = rec.get("langs") or [""]
            out[url] = _mention(
                source="Bluesky",
                url=url,
                title=text[:140],
                text=text,
                published=pub.isoformat(),
                language=(langs[0] or "")[:2],
                outlet="@" + handle,
            )
        time.sleep(1)
    _check(tries, fails, last_err)
    log.info("Bluesky: %d posts", len(out))
    return list(out.values())


COLLECTORS = {
    "gdelt": fetch_gdelt,
    "google_news": fetch_google_news,
    "reddit": fetch_reddit,
    "bluesky": fetch_bluesky,
}


def collect_all(config: dict, start: datetime, end: datetime) -> tuple[list[dict], dict]:
    """Run every enabled collector. Returns (mentions, per-source status)."""
    mentions, status = [], {}
    for name, fn in COLLECTORS.items():
        if not config.get("sources", {}).get(name, False):
            status[name] = "disabled"
            continue
        try:
            got = fn(config, start, end)
            mentions.extend(got)
            status[name] = f"ok ({len(got)})"
        except Exception as e:  # noqa: BLE001
            log.exception("%s crashed", name)
            status[name] = f"error: {e}"
    return mentions, status
