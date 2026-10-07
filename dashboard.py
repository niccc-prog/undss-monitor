"""UNDSS Sentiment Monitor — live dashboard (Streamlit).

Run locally:   streamlit run dashboard.py
Deploy free:   share.streamlit.io  (see README)
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from monitor.countries import COUNTRIES

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEMO = DATA / "demo"

# ---- colours (diverging red <-> blue with grey neutral; status red for alerts)
NEG, NEU, POS = "#e34948", "#a8a7a2", "#2a78d6"
SENT_COLORS = {"negative": NEG, "neutral": NEU, "positive": POS}
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e7e6e2"

LANG_LABEL = {"en": "English", "fr": "French", "es": "Spanish", "ar": "Arabic", "other": "Other", "": "Unknown"}
ISO_NAME = {iso: names[0] for iso, names in COUNTRIES.items()}

st.set_page_config(page_title="UNDSS Sentiment Monitor", page_icon="🛡️", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; max-width: 1400px;}
      [data-testid="stMetricValue"] {font-size: 1.9rem;}
      .small-muted {color:#6b6a66; font-size:0.85rem;}
      .alert-box {border-left: 4px solid #d03b3b; background: #fdf0ef; padding: .8rem 1rem;
                  border-radius: 6px; margin-bottom: 1rem; color:#2b0b0b}
      .demo-box {border-left: 4px solid #fab219; background: #fff8e6; padding: .6rem 1rem;
                 border-radius: 6px; margin-bottom: 1rem; color:#3a2a00}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, "") or os.getenv(name, "")
    except Exception:  # noqa: BLE001
        return os.getenv(name, "")


@st.cache_data(ttl=600, show_spinner=False)
def load_data() -> tuple[pd.DataFrame, list, list, bool]:
    """Real data from DATA_URL (raw GitHub link) or the local data/ folder; demo data as a fallback."""
    base = _secret("DATA_URL").rstrip("/")
    df, runs, alerts, demo = None, [], [], False
    if base:
        try:
            df = pd.read_csv(io.StringIO(requests.get(f"{base}/mentions.csv", timeout=20).text), dtype=str, keep_default_na=False)
            runs = requests.get(f"{base}/runs.json", timeout=20).json()
            alerts = requests.get(f"{base}/alerts.json", timeout=20).json()
        except Exception:  # noqa: BLE001
            df = None
    if df is None and (DATA / "mentions.csv").exists():
        df = pd.read_csv(DATA / "mentions.csv", dtype=str, keep_default_na=False)
        runs = json.loads((DATA / "runs.json").read_text()) if (DATA / "runs.json").exists() else []
        alerts = json.loads((DATA / "alerts.json").read_text()) if (DATA / "alerts.json").exists() else []
    if (df is None or df.empty) and (DEMO / "mentions.csv").exists():
        df = pd.read_csv(DEMO / "mentions.csv", dtype=str, keep_default_na=False)
        runs = json.loads((DEMO / "runs.json").read_text()) if (DEMO / "runs.json").exists() else []
        alerts = json.loads((DEMO / "alerts.json").read_text()) if (DEMO / "alerts.json").exists() else []
        demo = True
    if df is None:
        df = pd.DataFrame()
    if not df.empty:
        df["published"] = pd.to_datetime(df["published"], utc=True, errors="coerce", format="ISO8601")
        df = df.dropna(subset=["published"])
        df["day"] = df["published"].dt.tz_convert(None).dt.normalize()
        df["criticism"] = df["criticism"].astype(str) == "True"
        df["lang_label"] = df["language"].map(lambda x: LANG_LABEL.get(x, x.upper() if x else "Unknown"))
        df["score"] = df["sentiment"].map({"negative": -1, "neutral": 0, "positive": 1}).fillna(0)
    return df, runs, alerts, demo


df_all, runs, alerts_log, is_demo = load_data()

# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------
st.title("🛡️ UNDSS Sentiment Monitor")
last_run = runs[0] if runs else None
if last_run:
    t = pd.to_datetime(last_run["time"]).tz_convert("America/New_York")
    health = " · ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in last_run.get("sources", {}).items())
    st.markdown(
        f'<div class="small-muted">Public news &amp; social media mentions of UNDSS in English, French, Spanish and Arabic · '
        f'Last updated {t:%d %b %Y, %H:%M} New York time<br>{health}</div>',
        unsafe_allow_html=True,
    )

if is_demo:
    st.markdown(
        '<div class="demo-box">⚠️ <b>Demo data.</b> These mentions are invented so you can see how the dashboard works. '
        "Real data appears here automatically once the monitor has run (see README).</div>",
        unsafe_allow_html=True,
    )

if df_all.empty:
    st.info("No data yet. Run `python run.py --backfill-days 90` or trigger the GitHub workflow.")
    st.stop()

# active alert banner (any alert in the last 24h)
now = datetime.now(timezone.utc)
recent_alerts = [a for a in alerts_log if pd.to_datetime(a["time"]) > now - timedelta(hours=24)]
if recent_alerts:
    a = recent_alerts[0]
    st.markdown(
        f'<div class="alert-box">🚨 <b>{a["subject"]}</b><br>{a["intro"]}</div>',
        unsafe_allow_html=True,
    )

# --------------------------------------------------------------------------
# filters (sidebar)
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    min_d, max_d = df_all["day"].min().date(), df_all["day"].max().date()
    preset = st.radio("Period", ["7 days", "30 days", "90 days", "All", "Custom"], index=1, horizontal=True)
    if preset == "Custom":
        rng = st.date_input("Date range", (max(min_d, max_d - timedelta(days=30)), max_d), min_value=min_d, max_value=max_d)
        start_d, end_d = (rng if isinstance(rng, tuple) and len(rng) == 2 else (min_d, max_d))
    else:
        days = {"7 days": 7, "30 days": 30, "90 days": 90, "All": 10_000}[preset]
        start_d, end_d = max(min_d, now.date() - timedelta(days=days - 1)), max(max_d, now.date())

    sources = sorted(df_all["source"].unique())
    sel_src = st.multiselect("Sources", sources, default=sources)
    langs = sorted(df_all["lang_label"].unique())
    sel_lang = st.multiselect("Languages", langs, default=langs)
    all_topics = sorted({t for ts in df_all["topics"] for t in ts.split("|") if t})
    sel_topics = st.multiselect("Topics", all_topics, default=all_topics)
    crit_only = st.toggle("Only mentions critical of UNDSS", value=False)
    st.divider()
    st.caption(
        "Sentiment: GDELT news uses GDELT's full-article tone; other sources use a multilingual AI model on the "
        "headline/post. 'Critical' = contains criticism keywords (see config.yaml)."
    )

mask = (
    (df_all["day"].dt.date >= start_d)
    & (df_all["day"].dt.date <= end_d)
    & df_all["source"].isin(sel_src)
    & df_all["lang_label"].isin(sel_lang)
    & df_all["topics"].map(lambda ts: any(t in sel_topics for t in ts.split("|")))
)
if crit_only:
    mask &= df_all["criticism"]
df = df_all[mask].copy()

# previous period of equal length, for deltas
span = (end_d - start_d).days + 1
prev_mask = (
    (df_all["day"].dt.date >= start_d - timedelta(days=span))
    & (df_all["day"].dt.date < start_d)
    & df_all["source"].isin(sel_src)
    & df_all["lang_label"].isin(sel_lang)
)
prev = df_all[prev_mask]

# --------------------------------------------------------------------------
# KPI row
# --------------------------------------------------------------------------
def pct_neg(d: pd.DataFrame) -> float:
    return 100 * (d["sentiment"] == "negative").mean() if len(d) else 0.0


def net(d: pd.DataFrame) -> float:
    return 100 * d["score"].mean() if len(d) else 0.0


k1, k2, k3, k4 = st.columns(4)
has_prev = len(prev) > 0
k1.metric("Mentions", f"{len(df):,}", f"{len(df) - len(prev):+,} vs previous period" if has_prev else None)
k2.metric(
    "Negative share", f"{pct_neg(df):.0f}%",
    f"{pct_neg(df) - pct_neg(prev):+.0f} pts" if has_prev else None, delta_color="inverse",
)
k3.metric(
    "Net sentiment", f"{net(df):+.0f}",
    f"{net(df) - net(prev):+.0f}" if has_prev else None,
    help="% positive minus % negative. Ranges from −100 (all negative) to +100 (all positive).",
)
k4.metric("Critical of UNDSS", f"{int(df['criticism'].sum()):,}", help="Mentions containing criticism keywords")

if df.empty:
    st.warning("No mentions match these filters.")
    st.stop()


# --------------------------------------------------------------------------
# chart helpers
# --------------------------------------------------------------------------
def style(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK2, size=13),
        hoverlabel=dict(bgcolor="white", font_color=INK),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None),
        bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID, ticks="")
    fig.update_yaxes(gridcolor=GRID, zeroline=False, rangemode="tozero")
    return fig


days_index = pd.date_range(pd.Timestamp(start_d), pd.Timestamp(min(end_d, now.date())), freq="D")
daily = (
    df.groupby(["day", "sentiment"]).size().unstack(fill_value=0)
    .reindex(days_index, fill_value=0)
    .reindex(columns=["negative", "neutral", "positive"], fill_value=0)
)

# --------------------------------------------------------------------------
# row 1: volume + net sentiment trend
# --------------------------------------------------------------------------
c1, c2 = st.columns([3, 2])
with c1:
    st.subheader("Daily mentions by sentiment")
    fig = go.Figure()
    for s in ["negative", "neutral", "positive"]:
        fig.add_bar(
            x=daily.index, y=daily[s], name=s.capitalize(), marker_color=SENT_COLORS[s],
            marker_line=dict(color="white", width=1),
            hovertemplate="%{x|%d %b}<br>" + s.capitalize() + ": %{y}<extra></extra>",
        )
    fig.update_layout(barmode="stack", hovermode="x unified")
    st.plotly_chart(style(fig), width="stretch")

with c2:
    st.subheader("Net sentiment (7-day rolling)")
    tot = daily.sum(axis=1)
    roll = ((daily["positive"] - daily["negative"]).rolling(7, min_periods=1).sum()
            / tot.rolling(7, min_periods=1).sum().replace(0, pd.NA) * 100)
    fig = go.Figure()
    fig.add_hline(y=0, line_color="#b9b8b3", line_width=1)
    fig.add_scatter(
        x=roll.index, y=roll, mode="lines", line=dict(color=INK, width=2), name="Net sentiment",
        hovertemplate="%{x|%d %b}<br>Net sentiment: %{y:+.0f}<extra></extra>", connectgaps=True,
    )
    fig.update_yaxes(range=[-100, 100], rangemode="normal", ticksuffix="")
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(style(fig, legend=False), width="stretch")
    st.caption("Above 0 = more positive than negative coverage; below 0 = more negative.")

# --------------------------------------------------------------------------
# row 2: topics + map
# --------------------------------------------------------------------------
c3, c4 = st.columns([2, 3])
with c3:
    st.subheader("Topics")
    tp = df.assign(topic=df["topics"].str.split("|")).explode("topic")
    tp = tp[tp["topic"].isin(sel_topics)]
    tcount = tp.groupby(["topic", "sentiment"]).size().unstack(fill_value=0).reindex(
        columns=["negative", "neutral", "positive"], fill_value=0)
    tcount = tcount.loc[tcount.sum(axis=1).sort_values().index]
    fig = go.Figure()
    for s in ["negative", "neutral", "positive"]:
        fig.add_bar(
            y=tcount.index, x=tcount[s], name=s.capitalize(), orientation="h", marker_color=SENT_COLORS[s],
            marker_line=dict(color="white", width=1),
            hovertemplate="%{y}<br>" + s.capitalize() + ": %{x}<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    st.plotly_chart(style(fig, height=320), width="stretch")

with c4:
    st.subheader("Countries mentioned")
    cp = df.assign(iso=df["countries"].str.split("|")).explode("iso")
    cp = cp[cp["iso"].astype(bool) & cp["iso"].notna()]
    if cp.empty:
        st.info("No country names detected in these mentions.")
    else:
        cagg = cp.groupby("iso").agg(mentions=("id", "count"), neg=("sentiment", lambda s: (s == "negative").mean() * 100))
        cagg["name"] = cagg.index.map(ISO_NAME)
        fig = go.Figure(go.Choropleth(
            locations=cagg.index, z=cagg["mentions"], text=cagg["name"],
            colorscale=[[i / (len(SEQ) - 1), c] for i, c in enumerate(SEQ)],
            marker_line_color="white", marker_line_width=0.5,
            customdata=cagg["neg"],
            hovertemplate="<b>%{text}</b><br>Mentions: %{z}<br>Negative: %{customdata:.0f}%<extra></extra>",
            colorbar=dict(title="Mentions", thickness=10, len=0.7),
        ))
        fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth",
                        showcountries=True, countrycolor="#d8d7d2", landcolor="#f0efec", bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(style(fig, height=320, legend=False), width="stretch")

# --------------------------------------------------------------------------
# row 3: sources & languages
# --------------------------------------------------------------------------
c5, c6 = st.columns(2)
for col, field, title in [(c5, "source", "By source"), (c6, "lang_label", "By language")]:
    with col:
        st.subheader(title)
        g = df.groupby([field, "sentiment"]).size().unstack(fill_value=0).reindex(
            columns=["negative", "neutral", "positive"], fill_value=0)
        g = g.loc[g.sum(axis=1).sort_values().index]
        fig = go.Figure()
        for s in ["negative", "neutral", "positive"]:
            fig.add_bar(y=g.index, x=g[s], name=s.capitalize(), orientation="h", marker_color=SENT_COLORS[s],
                        marker_line=dict(color="white", width=1),
                        hovertemplate="%{y}<br>" + s.capitalize() + ": %{x}<extra></extra>")
        fig.update_layout(barmode="stack")
        st.plotly_chart(style(fig, height=240), width="stretch")

# --------------------------------------------------------------------------
# mentions table
# --------------------------------------------------------------------------
st.subheader("Mentions")
tab_neg, tab_crit, tab_all = st.tabs(["Latest negative", "Critical of UNDSS", "All"])
cols = ["published", "sentiment", "title", "outlet", "source", "lang_label", "topics", "url"]
cfg = {
    "published": st.column_config.DatetimeColumn("Date", format="D MMM YYYY, HH:mm"),
    "sentiment": st.column_config.TextColumn("Sentiment"),
    "title": st.column_config.TextColumn("Headline / post", width="large"),
    "outlet": "Outlet",
    "source": "Source",
    "lang_label": "Language",
    "topics": st.column_config.TextColumn("Topics"),
    "url": st.column_config.LinkColumn("Link", display_text="Open ↗"),
}


def table(d: pd.DataFrame):
    d = d.sort_values("published", ascending=False)[cols].copy()
    d["topics"] = d["topics"].str.replace("|", ", ", regex=False)
    d["sentiment"] = d["sentiment"].map({"negative": "🔴 Negative", "neutral": "⚪ Neutral", "positive": "🔵 Positive"})
    st.dataframe(d, column_config=cfg, hide_index=True, width="stretch", height=420)


with tab_neg:
    table(df[df["sentiment"] == "negative"])
with tab_crit:
    table(df[df["criticism"]])
with tab_all:
    table(df)

st.download_button(
    "Download filtered mentions (CSV)",
    df.drop(columns=["day", "score"]).to_csv(index=False).encode("utf-8"),
    file_name=f"undss_mentions_{start_d}_{end_d}.csv",
    mime="text/csv",
)

# --------------------------------------------------------------------------
# alerts + method notes
# --------------------------------------------------------------------------
with st.expander(f"Alert history ({len(alerts_log)})"):
    if not alerts_log:
        st.write("No alerts yet.")
    for a in alerts_log[:30]:
        t = pd.to_datetime(a["time"]).tz_convert("America/New_York")
        st.markdown(f"**{t:%d %b %Y %H:%M}** — {a['subject']}  \n{a['intro']}")
        for it in a.get("items", [])[:5]:
            st.markdown(f"- [{it['title']}]({it['url']}) — {it.get('outlet') or it.get('source', '')}")

with st.expander("How this works & limitations"):
    st.markdown(
        """
**Sources.** GDELT (global news in 100+ languages), Google News (EN/FR/ES/AR editions), Reddit and Bluesky.
X/Twitter, Facebook and LinkedIn are not included because their data is no longer freely available.

**Sentiment** describes the *tone of the coverage*, not necessarily the opinion of UNDSS. A neutral report that
"UNDSS evacuated staff after an attack" can score negative because the story is about violence. Use the
**Critical of UNDSS** view to see mentions that actually criticise UN security arrangements, and spot-check
headlines before drawing conclusions.

**Topics and countries** are assigned by keyword matching (editable in `config.yaml`). A mention can have
several topics. Countries are the ones *named in the text*, not where the outlet is based.

**Alerts** fire when negative mentions in the last 24 hours are at least twice the previous week's daily
average (and at least 3), or when a new critical mention appears.
"""
    )
