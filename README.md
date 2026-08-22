# BeatIt — Oncology Case Research

A local-first web application for organizing oncology research material (notes, URLs, PDFs, imaging, transcripts) and synthesizing insights with **sourced** AI analysis via **Ollama** or **OpenRouter**.

**Project page:** [simonbrightman.com/projects/beatit/](https://simonbrightman.com/projects/beatit/) · **License:** MIT

## Medical disclaimer

BeatIt supports research and case organization. It is **not** medical advice and does not replace evaluation by qualified oncology teams. Verify all clinical decisions with your care team.

## Features

- **Library ingest** — clinical notes, URLs, PDFs, YouTube transcripts, video files, Facebook links, DICOM imaging
- **Home assessment** — baseline synthesis with executive summary, open items, and numbered source references
- **Custom tasks** — focused queries with draft/refine workflow
- **AI Chat** — multi-turn treatment-options discussion scoped to your library and current assessment
- **Chat observations** — pin excerpts from chat into the next assessment or save them to the library
- **Imaging vision** — optional slice-level reads via Ollama vision models
- **PDF export** — download assessments with references
- **Deploy** — Render blueprint with optional auth and persistent disk

## Quick start

### 1. Configure LLM

```bash
cp .env.example .env
```

**OpenRouter:**

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

**Ollama (local or remote):**

```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://your-remote-server:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
```

**Auto (Ollama when reachable, otherwise OpenRouter):**

```env
LLM_PROVIDER=auto
OPENROUTER_API_KEY=sk-or-v1-your-key-here
OLLAMA_BASE_URL=http://100.x.x.x:11434
```

### 2. Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8080**

Or use `./scripts/start_local.sh` after configuring `.env`.

## Source citations

Assessments and chat replies use `[SOURCE: …]` tags (Document, Web, Chat observation, Patient context, AI inference). The UI maps inline numbers to a references list tied to library records where possible.

## API overview

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | App + LLM status |
| `/api/documents` | GET | List stored documents |
| `/api/ingest/text` | POST | Save clinical notes |
| `/api/analyze` | POST | Run baseline or custom analysis (async job) |
| `/api/options-chat/sessions` | GET/POST | AI Chat sessions |
| `/api/options-chat/observations` | GET/POST | Pin chat excerpts for analysis |

See route definitions in `app/api/routes.py` for the full API.

## Architecture

```
app/
  ingest/       # URL, PDF, YouTube, text, video, imaging handlers
  storage/      # SQLite + filesystem
  services/     # LLM clients, synthesis, chat observations, PDF export
  static/       # Web UI
  api/          # FastAPI routes
data/           # Created at runtime (gitignored)
```

## Remote Ollama over Tailscale

Step-by-step guide: **[docs/TAILSCALE_OLLAMA_SETUP.md](docs/TAILSCALE_OLLAMA_SETUP.md)**

```bash
./scripts/check_ollama.sh http://100.x.x.x:11434
```

## Deploy to Render

1. Connect this repo in the Render dashboard → **Blueprint** (`render.yaml`)
2. Set secrets: `AUTH_USERNAME`, `AUTH_PASSWORD`, `OPENROUTER_API_KEY`
3. Optional: `AUTH_USER_PASSWORDS` for per-user password overrides

When auth vars are set, visitors see a sign-in page. Leave them empty for local development.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).
