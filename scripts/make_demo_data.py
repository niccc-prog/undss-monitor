"""Create INVENTED demo data so the dashboard can be previewed before real data exists.

    python scripts/make_demo_data.py

Writes to data/demo/ (never mixed with real data). The dashboard shows a clear
"Demo data" banner whenever it is displaying these. Outlets and links are fictional.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor.analysis import countries_mentioned, is_criticism, tag_topics  # noqa: E402

random.seed(7)
NOW = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
OUT = ROOT / "data" / "demo"

PLACES = ["Sudan", "Haiti", "Yemen", "Lebanon", "Gaza", "Somalia", "Mali", "Afghanistan", "Ukraine",
          "South Sudan", "DR Congo", "Myanmar", "Nigeria", "Syria", "Colombia"]

T = {
    "en": {
        "negative": [
            "UN convoy attacked near {c}; UNDSS reviewing security procedures",
            "Aid workers killed in {c} as UN raises security level",
            "Staff union criticizes UNDSS over slow evacuation from {c}",
            "UNDSS accused of risk aversion limiting aid access in {c}",
            "Explosion near UN compound in {c}, staff relocated",
        ],
        "neutral": [
            "UNDSS updates travel advisory for {c}",
            "UN Department of Safety and Security publishes annual report",
            "UN staff in {c} told to follow new curfew, says UNDSS",
            "UNDSS official briefs member states on field security",
        ],
        "positive": [
            "UNDSS praised for safe evacuation of UN staff from {c}",
            "New UN security training welcomed by humanitarian partners",
            "UNDSS and NGOs agree joint security coordination in {c}",
        ],
    },
    "fr": {
        "negative": ["Attaque contre un convoi de l'ONU au {c}", "Le personnel de l'ONU évacué après une embuscade au {c}"],
        "neutral": ["L'UNDSS relève le niveau de sécurité au {c}", "Le Département de la sûreté et de la sécurité publie un rapport"],
        "positive": ["Évacuation réussie du personnel de l'ONU au {c}, salue l'UNDSS"],
    },
    "es": {
        "negative": ["Ataque contra convoy de la ONU en {c} deja heridos", "Critican a UNDSS por fracaso en la protección del personal"],
        "neutral": ["UNDSS actualiza el nivel de seguridad en {c}"],
        "positive": ["Elogian a UNDSS por apoyo a trabajadores humanitarios en {c}"],
    },
    "ar": {
        "negative": ["هجوم على قافلة للأمم المتحدة في {c}"],
        "neutral": ["إدارة شؤون السلامة والأمن تحدث مستوى الأمن في {c}"],
        "positive": ["نجاح إجلاء موظفي الأمم المتحدة من {c}"],
    },
}
AR_PLACES = {"Sudan": "السودان", "Yemen": "اليمن", "Lebanon": "لبنان", "Gaza": "غزة", "Somalia": "الصومال", "Syria": "سوريا"}
FR_PLACES = {"Mali": "Mali", "Haiti": "Haïti", "Lebanon": "Liban", "Sudan": "Soudan", "Niger": "Niger", "Syria": "Syrie"}

SOURCES = [("GDELT news", 0.5), ("Google News", 0.3), ("Reddit", 0.08), ("Bluesky", 0.12)]
OUTLETS = {"GDELT news": ["globalwire.example", "dailyreport.example", "worldnews.example", "lemonde-demo.example"],
           "Google News": ["Example Times", "Demo Herald", "Sample Gazette", "El Ejemplo"],
           "Reddit": ["r/UnitedNations", "r/humanitarian", "r/worldnews"],
           "Bluesky": ["@reporter.example", "@aidworker.example", "@analyst.example"]}


def pick(weights):
    r, acc = random.random(), 0
    for k, w in weights:
        acc += w
        if r <= acc:
            return k
    return weights[-1][0]


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    rows = []
    for d in range(90, -1, -1):
        day = NOW - timedelta(days=d)
        n = random.randint(2, 6)
        # a news event 40 days ago and a spike in the last 24h
        if 38 <= d <= 41:
            n += 6
        if d == 0:
            n += 9
        for i in range(n):
            lang = pick([("en", 0.62), ("fr", 0.16), ("es", 0.12), ("ar", 0.10)])
            p_neg = 0.75 if (d == 0 or 38 <= d <= 41) else 0.33
            sent = pick([("negative", p_neg), ("neutral", (1 - p_neg) * 0.65), ("positive", 1)])
            place = random.choice(PLACES)
            if lang == "ar":
                place = random.choice(list(AR_PLACES))
                cname = AR_PLACES[place]
            elif lang == "fr":
                place = random.choice(list(FR_PLACES))
                cname = FR_PLACES[place]
            else:
                cname = place
            title = random.choice(T[lang][sent]).format(c=cname)
            src = pick(SOURCES)
            pub = day - timedelta(hours=random.randint(0, 23), minutes=random.randint(0, 59))
            uid = f"demo{d:03d}{i:02d}"
            score = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}[sent]
            rows.append({
                "id": uid, "published": pub.isoformat(), "first_seen": pub.isoformat(), "source": src,
                "outlet": random.choice(OUTLETS[src]), "outlet_country": "", "language": lang,
                "title": title, "url": f"https://example.org/demo/{uid}", "sentiment": sent,
                "sentiment_score": score, "sentiment_engine": "demo",
                "topics": "|".join(tag_topics(title, cfg["topics"])),
                "criticism": is_criticism(title, cfg["criticism_keywords"]) and sent == "negative",
                "countries": "|".join(countries_mentioned(title)), "text": title,
            })
    df = pd.DataFrame(rows).sort_values("published", ascending=False)
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "mentions.csv", index=False)

    last24 = df[pd.to_datetime(df["published"], format="ISO8601") > NOW - timedelta(hours=24)]
    neg24 = last24[last24["sentiment"] == "negative"]
    (OUT / "runs.json").write_text(json.dumps([{
        "time": NOW.isoformat(), "sources": {"gdelt": "demo", "google_news": "demo", "reddit": "demo", "bluesky": "demo"},
        "collected": len(df), "new": 0, "total": len(df), "sentiment_engine": "demo", "alerts": ["spike"]}], indent=2))
    (OUT / "alerts.json").write_text(json.dumps([{
        "time": (NOW - timedelta(hours=1)).isoformat(), "kind": "spike",
        "subject": f"⚠️ UNDSS monitor: negative coverage spike ({len(neg24)} in 24h) — DEMO",
        "intro": f"{len(neg24)} negative mentions in the last 24 hours — well above the recent daily average.",
        "items": neg24.head(5)[["title", "url", "outlet", "source"]].to_dict("records"), "delivered_via": []}],
        ensure_ascii=False, indent=2))
    print(f"Demo data: {len(df)} invented mentions -> {OUT}")


if __name__ == "__main__":
    main()
