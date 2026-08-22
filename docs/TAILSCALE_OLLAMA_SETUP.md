# Tailscale VM + Ollama setup for BeatIt

Use this guide to run LLMs on a private machine (Windows or Linux) and connect BeatIt over **Tailscale**.

**VM specs (reference):** 4 vCPU, 16 GB RAM — sized for one **7–8B text model** at a time, or occasional **vision** on a few CT slices.

**Do not commit passwords or API keys.** Keep credentials in a password manager, not in `.env` or git.

---

## Overview

| Where | Role |
|-------|------|
| **Render (`main`)** | App + data; typically **OpenRouter** |
| **Your laptop (local dev)** | BeatIt; **`LLM_PROVIDER=auto`** → VM when up, else OpenRouter |
| **Private VM (Tailscale)** | **Ollama** on port 11434 |

Tailscale IP example: `100.x.x.x` — use `tailscale ip -4` on the VM if it changes.

---

## Step 1 — Confirm Tailscale on the VM

On your VM (RDP or local console):

1. Open the Tailscale app — status should be **Connected**.
2. Note the Tailscale IPv4 (e.g. `100.x.x.x`).

On your **development machine**:

```bash
ping -c 2 100.x.x.x
# or
ping -c 2 your-hostname.tail-scale.ts.net
```

If ping fails, fix Tailscale on both machines before continuing.

---

## Step 2 — Install Ollama on Windows

1. Download **Ollama for Windows**: https://ollama.com/download  
2. Run the installer (default is fine).  
3. Open **PowerShell as Administrator** and set Ollama to listen on all interfaces (needed for Tailscale):

```powershell
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "Machine")
```

4. Restart the **Ollama** app (tray icon → Quit, then start again from Start menu).

Verify locally on the VM:

```powershell
curl http://127.0.0.1:11434/api/tags
```

You should get JSON with `"models": []` or a list of models.

---

## Step 3 — Windows Firewall (Tailscale only)

Allow inbound **TCP 11434** on the **Tailscale** network adapter only (not public LAN):

1. **Windows Defender Firewall** → **Advanced settings** → **Inbound Rules** → **New Rule…**
2. Port → TCP → **11434**
3. Allow the connection
4. Profile: customize so it applies to the network profile Tailscale uses (often Private)
5. Name: `Ollama Tailscale`

Optional PowerShell (review before running):

```powershell
New-NetFirewallRule -DisplayName "Ollama Tailscale" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow
```

---

## Step 4 — Pull models (16 GB RAM)

On the VM, in PowerShell or `cmd`:

```powershell
# Primary text model for case synthesis (recommended)
ollama pull qwen2.5:7b-instruct

# Optional: faster/smaller for quick summaries
ollama pull llama3.2:3b

# Vision for Imaging tab (recommended — works without mllama)
ollama pull moondream
```

Check:

```powershell
ollama list
```

**Avoid** on 16 GB: 32B+ models, or running two large models at once.

---

## Step 5 — Test from your dev machine

```bash
cd /path/to/beatit
./scripts/check_ollama.sh http://100.x.x.x:11434
```

Or:

```bash
curl -s http://100.x.x.x:11434/api/tags | python3 -m json.tool
```

Expected: HTTP 200 and your pulled model names.

---

## Step 6 — Configure BeatIt (local `.env` only)

Edit **local** `.env` (never commit secrets):

```env
LLM_PROVIDER=auto

# Tailscale VM
OLLAMA_BASE_URL=http://100.x.x.x:11434
OLLAMA_MODEL=qwen2.5:7b-instruct
OLLAMA_VISION_MODEL=moondream

# Fallback when VM is off
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=openai/gpt-4o-mini
```

- **`auto`** — uses Ollama when the VM responds; otherwise OpenRouter.  
- **`ollama`** — VM only (fails if VM is down).  
- **`openrouter`** — cloud only (same as Render).

---

## Step 7 — Run BeatIt locally

```bash
source .venv/bin/activate
python run.py
```

Open http://localhost:8080 → **Settings** → confirm LLM connectivity.

Run a small custom task or baseline with a **narrow document scope** first (text-only) to validate latency.

---

## Step 8 — Production (Render)

On Render, keep:

```env
LLM_PROVIDER=openrouter
```

The private VM is **not** reachable from Render unless you add Tailscale to Render (advanced). Local dev gets private LLM; production typically uses OpenRouter.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dev machine cannot reach `:11434` | Tailscale connected? Firewall rule? `OLLAMA_HOST=0.0.0.0`? Restart Ollama |
| `model_available: false` | Run `ollama pull` for the name in `OLLAMA_MODEL` |
| Very slow responses | Normal on CPU; use smaller model or increase VM resources |
| BeatIt still uses OpenRouter | `LLM_PROVIDER=auto` and VM health must pass; check `./scripts/check_ollama.sh` |
| Vision fails | Update Ollama from https://ollama.com/download, or use `OLLAMA_VISION_MODEL=moondream` |
