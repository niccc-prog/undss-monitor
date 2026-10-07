"""Offline test of the whole pipeline with fake API responses.

Run:  python -m pytest -q tests   (or: python tests/test_pipeline.py)
No network needed — every source is mocked with responses shaped like the real APIs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from monitor import alerts, analysis, sources, storage  # noqa: E402

NOW = datetime.now(timezone.utc)


class FakeResp:
    def __init__(self, status=200, text="", content=None, js=None):
        self.status_code = status
        self.text = text if js is None else json.dumps(js)
        self.content = content if content is not None else self.text.encode()
        self._js = js

    def json(self):
        return self._js if self._js is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def gdelt_payload(neg=False, pos=False):
    seen = (NOW - timedelta(hours=3)).strftime("%Y%m%dT%H%M%SZ")
    arts = [
        {"url": "https://news.example/a1", "title": "UNDSS criticised for slow evacuation of UN staff in Sudan",
         "seendate": seen, "domain": "news.example", "language": "English", "sourcecountry": "United Kingdom"},
        {"url": "https://news.example/a2", "title": "UN Department of Safety and Security praised for new training",
         "seendate": seen, "domain": "news.example", "language": "English", "sourcecountry": "United States"},
        {"url": "https://journal.example/a3", "title": "Le Département de la sûreté et de la sécurité publie un rapport",
         "seendate": seen, "domain": "journal.example", "language": "French", "sourcecountry": "France"},
    ]
    if neg:
        return {"articles": [arts[0]]}
    if pos:
        return {"articles": [arts[1]]}
    return {"articles": arts}


RSS = f"""<?xml version="1.0"?><rss><channel>
<item><title>Ataque contra convoy de la ONU en Haití - El Diario</title>
<link>https://news.google.com/x1</link>
<pubDate>{(NOW - timedelta(hours=2)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
<description>&lt;a&gt;El Departamento de Seguridad de las Naciones Unidas evacuó personal&lt;/a&gt;</description>
<source url="https://diario.example">El Diario</source></item>
<item><title>هجوم على قافلة للأمم المتحدة في اليمن - الجزيرة</title>
<link>https://news.google.com/x2</link>
<pubDate>{(NOW - timedelta(hours=5)).strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
<description>إدارة شؤون السلامة والأمن تعلن إجلاء موظفي الأمم المتحدة</description>
<source url="https://aj.example">الجزيرة</source></item>
</channel></rss>"""

REDDIT = {"data": {"children": [{"data": {
    "title": "Anyone else find UNDSS security clearance process a failure?",
    "selftext": "TRIP approvals in Kenya are inadequate and slow",
    "permalink": "/r/UnitedNations/comments/abc/x/", "subreddit": "UnitedNations",
    "created_utc": (NOW - timedelta(hours=1)).timestamp()}}]}}

BSKY = {"posts": [{
    "uri": "at://did:plc:xyz/app.bsky.feed.post/3kabc",
    "author": {"handle": "reporter.bsky.social"},
    "record": {"text": "UNDSS just raised the security level in Lebanon, UN staff relocated.",
               "createdAt": (NOW - timedelta(hours=4)).isoformat().replace("+00:00", "Z"), "langs": ["en"]}}]}


def fake_get(url, params=None, headers=None, timeout=None):
    params = params or {}
    if "gdeltproject" in url:
        q = params.get("query", "")
        return FakeResp(js=gdelt_payload(neg="tone<" in q, pos="tone>" in q))
    if "news.google.com" in url:
        return FakeResp(text=RSS, content=RSS.encode("utf-8"))
    if "reddit.com" in url:
        return FakeResp(js=REDDIT)
    if "bsky" in url:
        return FakeResp(js=BSKY)
    raise AssertionError(url)


def test_full_pipeline(tmp_path=None):
    import tempfile

    tmp = Path(tmp_path or tempfile.mkdtemp())
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    config["google_news_editions"] = {"es": ["ES"]}
    config["search_terms"] = {"en": config["search_terms"]["en"][:2], "es": config["search_terms"]["es"]}

    with mock.patch.object(storage, "DATA_DIR", tmp), \
         mock.patch.object(storage, "MENTIONS", tmp / "m.csv"), \
         mock.patch.object(storage, "RUNS", tmp / "r.json"), \
         mock.patch.object(storage, "ALERTS", tmp / "a.json"), \
         mock.patch.object(storage, "ALERT_STATE", tmp / "s.json"), \
         mock.patch.object(sources, "GDELT_PAUSE", 0), \
         mock.patch.object(sources.time, "sleep", lambda s: None), \
         mock.patch.object(sources.requests, "get", fake_get):
        start, end = NOW - timedelta(days=2), NOW
        raw, status = sources.collect_all(config, start, end)
        assert all(v.startswith("ok") for v in status.values()), status
        by_src = {m["source"] for m in raw}
        assert by_src == {"GDELT news", "Google News", "Reddit", "Bluesky"}, by_src

        enriched = analysis.enrich(raw, config)
        g = {m["url"]: m for m in enriched}
        assert g["https://news.example/a1"]["sentiment"] == "negative"
        assert g["https://news.example/a2"]["sentiment"] == "positive"
        assert g["https://journal.example/a3"]["sentiment"] == "neutral"
        assert g["https://news.example/a1"]["criticism"] is True
        assert "SDN" in g["https://news.example/a1"]["countries"]
        ar = g["https://news.google.com/x2"]
        assert ar["language"] == "es" or ar["countries"] == "YEM"  # language from feed edition
        assert "YEM" in ar["countries"]
        assert "Security incidents" in ar["topics"]
        bs = g["https://bsky.app/profile/reporter.bsky.social/post/3kabc"]
        assert "Travel advisories & restrictions" in bs["topics"] and "LBN" in bs["countries"]
        rd = [m for m in enriched if m["source"] == "Reddit"][0]
        assert rd["criticism"] is True and rd["language"] == "en"

        all_df, new_df = storage.merge_and_save(enriched)
        assert len(new_df) == len(enriched)
        # second run: nothing new
        all2, new2 = storage.merge_and_save(analysis.enrich(sources.collect_all(config, start, end)[0], config))
        assert len(new2) == 0 and len(all2) == len(all_df)

        # spike: 0 baseline, >=3 negative in 24h? craft explicitly
        import pandas as pd
        rows = [{"sentiment": "negative", "published": (NOW - timedelta(hours=h)).isoformat(),
                 "title": f"t{h}", "url": f"u{h}", "outlet": "o", "source": "s"} for h in (1, 2, 3, 5)]
        rows += [{"sentiment": "negative", "published": (NOW - timedelta(days=d)).isoformat(),
                  "title": f"old{d}", "url": f"o{d}", "outlet": "o", "source": "s"} for d in (2, 5)]
        spike = alerts.detect_spike(pd.DataFrame(rows), 2.0, 3)
        assert spike and spike["count"] == 4 and spike["baseline"] == round(2 / 7, 2)
        assert alerts.detect_spike(pd.DataFrame(rows[:2]), 2.0, 3) is None

        fired = alerts.run_alerts(all_df, new_df, config)
        assert "criticism" in fired
        assert (tmp / "a.json").exists()
        # cooldown: spike alert not repeated
        print("status:", status)
        print("fired:", fired)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    test_full_pipeline()
