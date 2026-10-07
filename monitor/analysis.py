"""Enrichment: language, sentiment, topics, criticism flag, countries mentioned."""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from .countries import CASE_SENSITIVE, COUNTRIES

log = logging.getLogger("monitor.analysis")

ARABIC_RE = re.compile(r"[؀-ۿ]")

_STOPWORDS = {
    "en": {"the", "and", "of", "to", "in", "is", "for", "on", "with", "that", "by", "from", "are", "was"},
    "fr": {"le", "la", "les", "des", "et", "est", "une", "du", "dans", "pour", "sur", "qui", "au", "avec"},
    "es": {"el", "los", "las", "del", "y", "es", "una", "en", "para", "por", "con", "que", "se", "al"},
}


def detect_language(text: str) -> str:
    if not text:
        return ""
    if len(ARABIC_RE.findall(text)) > 5:
        return "ar"
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    scores = {lang: sum(w in sw for w in words) for lang, sw in _STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


# --------------------------------------------------------------------------
# keyword matching (word boundaries for Latin script, substring for Arabic)
# --------------------------------------------------------------------------
@lru_cache(maxsize=4096)
def _pattern(term: str, case_sensitive: bool = False) -> re.Pattern:
    if ARABIC_RE.search(term):
        return re.compile(re.escape(term))
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", flags)


def _has(text: str, term: str, case_sensitive: bool = False) -> bool:
    return bool(_pattern(term, case_sensitive).search(text))


def tag_topics(text: str, topics: dict[str, list[str]]) -> list[str]:
    found = [name for name, kws in topics.items() if any(_has(text, k) for k in kws)]
    return found or ["Institution & leadership"]


def is_criticism(text: str, keywords: list[str]) -> bool:
    return any(_has(text, k) for k in keywords)


def countries_mentioned(text: str) -> list[str]:
    out = []
    for iso, names in COUNTRIES.items():
        for n in names:
            if _has(text, n, case_sensitive=n in CASE_SENSITIVE):
                out.append(iso)
                break
    return out


# --------------------------------------------------------------------------
# sentiment
# --------------------------------------------------------------------------
_LEXICON = {
    "negative": [
        "attack", "killed", "dead", "death", "violence", "crisis", "threat", "danger", "failure", "criticized",
        "criticised", "condemn", "kidnapped", "abducted", "injured", "wounded", "fear", "insecure", "unsafe",
        "attaque", "tué", "mort", "violence", "menace", "danger", "échec", "condamne", "blessé", "enlevé",
        "ataque", "muerto", "muerte", "violencia", "amenaza", "peligro", "fracaso", "condena", "herido", "secuestrado",
        "هجوم", "قتل", "مقتل", "عنف", "تهديد", "خطر", "فشل", "إدانة", "جرحى", "اختطاف",
    ],
    "positive": [
        "praised", "welcomed", "success", "successful", "safe", "improved", "commend", "thanks", "support",
        "protected", "rescued", "agreement", "progress",
        "salué", "succès", "sûr", "amélioré", "félicite", "soutien", "protégé", "progrès",
        "elogió", "éxito", "seguro", "mejorado", "apoyo", "protegido", "progreso", "acuerdo",
        "نجاح", "آمن", "تحسن", "دعم", "ترحيب", "تقدم", "حماية",
    ],
}


def _lexicon_sentiment(text: str) -> tuple[str, float]:
    neg = sum(_has(text, w) for w in _LEXICON["negative"])
    pos = sum(_has(text, w) for w in _LEXICON["positive"])
    if neg == pos:
        return "neutral", 0.0
    score = (pos - neg) / (pos + neg)
    return ("positive" if score > 0 else "negative"), round(score, 3)


class SentimentScorer:
    """Uses a free multilingual Hugging Face model if installed, else a keyword lexicon."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._pipe = None
        self.engine = "lexicon"
        try:
            from transformers import pipeline  # type: ignore

            self._pipe = pipeline(
                "sentiment-analysis", model=model_name, tokenizer=model_name, truncation=True, max_length=256
            )
            self.engine = "model"
            log.info("Sentiment: using model %s", model_name)
        except Exception as e:  # noqa: BLE001
            log.warning("Sentiment model unavailable (%s) — using keyword method.", e.__class__.__name__)

    def score(self, texts: list[str]) -> list[tuple[str, float]]:
        if not texts:
            return []
        if self._pipe is None:
            return [_lexicon_sentiment(t) for t in texts]
        results = self._pipe([t[:1000] or "." for t in texts], batch_size=16)
        out = []
        for r in results:
            label = r["label"].lower()
            label = {"label_0": "negative", "label_1": "neutral", "label_2": "positive"}.get(label, label)
            conf = float(r["score"])
            score = conf if label == "positive" else -conf if label == "negative" else 0.0
            out.append((label, round(score, 3)))
        return out


def enrich(mentions: list[dict], config: dict, scorer: SentimentScorer | None = None) -> list[dict]:
    if not mentions:
        return []
    scorer = scorer or SentimentScorer(config["sentiment"]["model"])
    texts = [f"{m.get('title', '')}. {m.get('text', '')}".strip(". ") for m in mentions]

    needs_model = [i for i, m in enumerate(mentions) if not m.get("gdelt_tone_label")]
    scored = dict(zip(needs_model, scorer.score([texts[i] for i in needs_model])))

    for i, m in enumerate(mentions):
        t = texts[i]
        if not m.get("language"):
            m["language"] = detect_language(t)
        if m.get("gdelt_tone_label"):
            m["sentiment"] = m["gdelt_tone_label"]
            m["sentiment_score"] = {"negative": -1.0, "neutral": 0.0, "positive": 1.0}[m["sentiment"]]
            m["sentiment_engine"] = "gdelt-tone"
        else:
            m["sentiment"], m["sentiment_score"] = scored[i]
            m["sentiment_engine"] = scorer.engine
        m["topics"] = "|".join(tag_topics(t, config["topics"]))
        m["criticism"] = is_criticism(t, config["criticism_keywords"])
        m["countries"] = "|".join(countries_mentioned(t))
    return mentions
