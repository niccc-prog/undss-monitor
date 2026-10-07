# UNDSS Sentiment Monitor

A free, automated system that tracks how UNDSS is talked about in news and on social media in **English, French, Spanish and Arabic**, scores the sentiment of each mention, and shows everything on a live dashboard with alerts when negative coverage spikes.

**Cost: €0.** It runs on free services only (GitHub Actions, Streamlit Community Cloud, public data sources) and needs no server.

---

## What it does

```
 Every 3 hours (GitHub Actions, free)
 ┌──────────────────────────────────────────────────────────────────┐
 │ 1. COLLECT   GDELT news · Google News · Reddit · Bluesky        │
 │ 2. ANALYSE   sentiment · topic · criticism flag · countries     │
 │ 3. SAVE      data/mentions.csv (duplicates removed)             │
 │ 4. ALERT     spike or new criticism → email / Telegram          │
 └──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
          Live dashboard (Streamlit Community Cloud, free)
```

| Part | How |
|---|---|
| **News** | [GDELT](https://www.gdeltproject.org/) — global news in 100+ languages, with its own full-article tone score. Plus Google News RSS for each language edition. |
| **Social media** | Reddit and Bluesky public posts. (X/Twitter and Facebook are excluded — their data is no longer free.) |
| **Sentiment** | GDELT tone for GDELT articles; a free multilingual AI model ([XLM-RoBERTa](https://huggingface.co/cardiffnlp/twitter-xlm-roberta-base-sentiment)) for everything else. |
| **Topics** | Security incidents · Travel advisories & restrictions · Staff safety · Institution & leadership (keyword lists in `config.yaml`). |
| **Criticism flag** | Separates mentions that *criticise* UN security arrangements from ones that simply report bad news. |
| **Countries** | Countries named in each mention, shown on a map. |
| **Alerts** | Negative mentions in the last 24h ≥ 2× the previous week's daily average (and ≥ 3), or any new critical mention. Sent by email and/or Telegram. |

---

## Setup (about 30 minutes, no coding)

You need a free **GitHub** account. That's all for the basic version.

### Step 1 — Put the project on GitHub
1. Go to [github.com/new](https://github.com/new). Name the repository `undss-monitor`. Choose **Public** (easiest, and the data is all public news) or **Private**. Click **Create repository**.
2. On the new page, click **uploading an existing file**. Unzip `undss-monitor.zip` on your computer and drag **all of its contents** (including the `.github` and `.streamlit` folders) into the browser. Click **Commit changes**.
   - On a Mac, folders starting with a dot are hidden. Press **Cmd + Shift + .** in Finder to show them.

### Step 2 — Run it for the first time (fills in the last 90 days)
1. In your repository, open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **UNDSS monitor** on the left → **Run workflow** → type `90` in the box → **Run workflow**.
3. Wait 10–20 minutes (the first run downloads the AI model). A green tick means it worked. A new file `data/mentions.csv` will appear in the repository.

From now on it runs automatically **every 3 hours**.

### Step 3 — Publish the dashboard
1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Repository: `your-username/undss-monitor` · Branch: `main` · Main file: `dashboard.py` → **Deploy**.
4. After a minute or two you'll get a link like `https://undss-monitor.streamlit.app`. That's your live dashboard. Share it with your supervisor.

The dashboard refreshes automatically when new data arrives. If your repository is **public**, you can make it pick up new data even faster: in the app's **Settings → Secrets**, add:
```toml
DATA_URL = "https://raw.githubusercontent.com/YOUR-USERNAME/undss-monitor/main/data"
```

> **Before you have real data**, the dashboard shows invented *demo* data with a yellow "Demo data" banner. It switches to real data automatically once Step 2 has run.

### Step 4 — Turn on alerts (optional)
In your repository: **Settings → Secrets and variables → Actions → New repository secret**.

**Email (Gmail):**
| Secret name | Value |
|---|---|
| `ALERT_EMAIL_TO` | who gets alerts (comma-separate several addresses) |
| `SMTP_USER` | the Gmail address that sends them |
| `SMTP_PASSWORD` | a Gmail **app password** — create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (needs 2-step verification on). Not your normal password. |

**Telegram (instant phone notifications):**
1. In Telegram, message **@BotFather** → `/newbot` → follow the steps → copy the token.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the `"chat":{"id": ...}` number.
3. Add secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

To put a dashboard link in every alert: **Settings → Secrets and variables → Actions → Variables** tab → add `DASHBOARD_URL` with your Streamlit link.

### Step 5 — Make social media sources reliable (optional)
- **Bluesky** search needs a free account. Create one at [bsky.app](https://bsky.app), then go to **Settings → Privacy and security → App passwords** and add secrets `BSKY_HANDLE` (e.g. `yourname.bsky.social`) and `BSKY_APP_PASSWORD`.
- **Reddit** often blocks anonymous requests from cloud servers. Create a free "script" app at [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) and add secrets `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`.

Without these, the monitor still works — those two sources are just skipped, and the dashboard header shows each source's status.

---

## Customising

Everything is in **`config.yaml`** (you can edit it directly on GitHub — click the file, then the pencil icon):
- **Search terms** for each language
- **Topics** and their keywords
- **Criticism keywords**
- **Alert sensitivity** (`spike_multiplier`, `spike_min_count`)
- Turn sources on or off

To change how often it runs, edit the `cron` line in `.github/workflows/monitor.yml` (`17 */3 * * *` = every 3 hours).

---

## Reading the results — important caveats

- **Sentiment measures the tone of the coverage, not opinion about UNDSS.** "UNDSS evacuated staff after an attack" will often score *negative* because it's about violence, even though it says nothing bad about UNDSS. Use the **Critical of UNDSS** view for actual criticism.
- **Automated scoring makes mistakes**, especially with sarcasm, short posts and Arabic. Spot-check the headlines before reporting numbers.
- **Volume is low.** UNDSS is a niche subject, so a few articles can move the percentages a lot. Look at the counts, not just the percentages.
- **Coverage gaps:** X/Twitter, Facebook, LinkedIn, WhatsApp and paywalled outlets are not included.
- **Public data only.** The tool collects nothing private.

---

## For technical users

```bash
pip install -r requirements.txt
python run.py --backfill-days 90     # collect (add -r requirements-model.txt for the AI model)
streamlit run dashboard.py           # view at http://localhost:8501
python scripts/make_demo_data.py     # regenerate demo data
python tests/test_pipeline.py        # offline test of the full pipeline
```

Project layout: `monitor/sources.py` (collectors) · `monitor/analysis.py` (sentiment, topics, countries) · `monitor/alerts.py` (spike detection, email/Telegram) · `monitor/storage.py` (CSV/JSON in `data/`) · `run.py` (one cycle) · `dashboard.py` (Streamlit).

**Note:** GitHub pauses scheduled workflows in public repositories after 60 days with no activity. If that happens, open the Actions tab and click **Enable workflow**.

**Ideas for later:** YouTube comments (free API key), Mastodon, a weekly PDF summary emailed to the team, human review of a sample to measure the model's accuracy on UNDSS content.
