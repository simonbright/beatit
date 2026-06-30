# BeatIt — Oncology Case Analysis

A local-first web application for storing clinical research material (notes, URLs, PDFs, YouTube transcripts, videos) and synthesizing oncology insights via **Ollama** on a remote server.

Built for a pancreatic cancer case review (woman in her 70s, possible liver metastasis). Task 1 covers **storage + medical synthesis**.

## Features (v0.1)

- **Ingest**
  - Clinical notes / free text
  - Web pages and URLs (HTML extraction; PDF URLs supported)
  - PDF uploads (text extraction via pypdf)
  - YouTube URLs (caption/transcript extraction)
  - Video files (stored locally; manual notes until transcription is connected)
- **Storage**
  - SQLite metadata + local files under `./data/`
- **Analysis**
  - Ollama-powered oncology synthesis with structured prompts
  - Baseline assessment, custom queries, document summarization
  - Analysis history persisted locally
- **UI**
  - Responsive web interface for desktop and mobile

## Quick start

### 1. Configure LLM

Copy the example env file:

```bash
cp .env.example .env
```

**OpenRouter (default until Ollama is ready):**

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

**Ollama (when your remote server is ready):**

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://your-remote-server:11434
OLLAMA_MODEL=llama3.2
```

**Auto (Ollama when available, otherwise OpenRouter):**

```env
LLM_PROVIDER=auto
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OLLAMA_BASE_URL=http://your-remote-server:11434
```

### 2. Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8080**

## API overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | App + LLM status (OpenRouter / Ollama) |
| `/api/documents` | GET | List stored documents |
| `/api/ingest/text` | POST | Save clinical notes |
| `/api/ingest/url` | POST | Fetch web page |
| `/api/ingest/youtube` | POST | Ingest transcript |
| `/api/ingest/pdf` | POST | Upload PDF |
| `/api/ingest/video` | POST | Upload video |
| `/api/analyze` | POST | Run oncology analysis |
| `/api/analyze/summarize` | POST | Summarize documents |
| `/api/analyses` | GET | Analysis history |

## Architecture

```
app/
  ingest/       # URL, PDF, YouTube, text, video handlers
  storage/      # SQLite + filesystem
  services/     # Ollama client + medical synthesis
  static/       # Web UI
  api/          # FastAPI routes
data/           # Created at runtime (gitignored)
```

## Emulated / future components

| Component | Current behavior | Future replacement |
|-----------|------------------|-------------------|
| Video transcription | Store file + manual notes | Whisper / remote ASR |
| Scanned PDFs | Placeholder if no text | OCR pipeline |
| Web fetch | Basic HTML extraction | Readability / headless browser |
| Vector search | Full corpus sent to Ollama | Embeddings + RAG |
| Auth | Cookie login when `AUTH_USERNAME` + `AUTH_PASSWORD` set | OAuth / invite-only |

## Medical disclaimer

This tool supports research and case organization. It is **not** medical advice and does not replace evaluation by qualified oncology teams.

## Remote Ollama notes

If Ollama runs on another machine, ensure:

1. `OLLAMA_HOST=0.0.0.0` on the server (or appropriate bind)
2. Firewall allows your client IP on port 11434
3. Use VPN/SSH tunnel if not exposing publicly

Example SSH tunnel:

```bash
ssh -L 11434:localhost:11434 user@remote-server
# then set OLLAMA_BASE_URL=http://localhost:11434
```

## Deploy to Render (secured)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial BeatIt app with OpenRouter and secured Render deployment"
gh repo create beatit --private --source=. --remote=origin --push
```

### 2. Create Render service

1. Open [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect the `beatit` GitHub repo — Render reads `render.yaml`
3. Set secret environment variables in the Render dashboard:
   - `AUTH_USERNAME` — one or more usernames, comma-separated (e.g. `simon,jane`)
   - `AUTH_PASSWORD` — strong password
   - `OPENROUTER_API_KEY` — your OpenRouter key

Or deploy manually: **New Web Service** → connect repo → use:

- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Health check path:** `/api/health`

### 3. Secured access

When `AUTH_USERNAME` and `AUTH_PASSWORD` are set, visitors see a **sign-in page** (no repeated browser password prompts). Multiple usernames share one password — set `AUTH_USERNAME=simon.brightman@gmail.com,dov@bright-man.com` on Render. Session lasts 7 days. `/api/health` stays public for Render health checks.

Leave auth vars empty for local development without a login prompt.

### 4. Persistent storage

`render.yaml` mounts a 1 GB disk at `/var/data` (Starter plan). Documents and SQLite DB persist across deploys. Without a disk, data is lost on redeploy.

### 5. Environment reference

| Variable | Required on Render | Description |
|----------|-------------------|-------------|
| `AUTH_USERNAME` | Yes | Comma-separated usernames (shared password) |
| `AUTH_PASSWORD` | Yes | Basic auth password |
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `DATA_DIR` | Auto (`/var/data`) | Storage path |
| `RENDER_EXTERNAL_URL` | Auto | Set by Render for OpenRouter referer |
