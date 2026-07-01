# Tailscale VM + Ollama setup for BeatIt

Use this guide to run LLMs on your private Windows VM (`W11VMAI01`) and connect BeatIt over Tailscale. Work happens on branch `feature/tailscale-ollama-vm`; `main` stays unchanged for production on Render.

**VM specs (reference):** 4 vCPU, 16 GB RAM, 110 GB disk — sized for one **7–8B text model** at a time, or occasional **vision** on a few CT slices later.

**Do not commit passwords or API keys.** Keep RDP credentials in a password manager, not in `.env` or git.

---

## Overview

| Where | Role |
|-------|------|
| **Render (`main`)** | App + data; stays on **OpenRouter** |
| **Your Mac (local dev)** | BeatIt; **`LLM_PROVIDER=auto`** → VM when up, else OpenRouter |
| **Windows VM (Tailscale)** | **Ollama** on port 11434 |

Tailscale IP example: `100.92.208.65` — use `tailscale ip -4` on the VM if it changes.

---

## Step 1 — Confirm Tailscale on the VM

On **W11VMAI01** (RDP or local console):

1. Open the Tailscale app — status should be **Connected**.
2. Note the Tailscale IPv4 (e.g. `100.92.208.65`).

On **your Mac**:

```bash
ping -c 2 100.92.208.65
# or
ping -c 2 w11vmai01.tailc529.ts.net
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

# Optional later: vision for selected CT slices (slow on CPU — use sparingly)
# ollama pull llama3.2-vision:11b
```

Check:

```powershell
ollama list
```

**Avoid** on 16 GB: 32B+ models, or running two large models at once.

---

## Step 5 — Test from your Mac

```bash
cd /path/to/beatit
./scripts/check_ollama.sh http://100.92.208.65:11434
```

Or:

```bash
curl -s http://100.92.208.65:11434/api/tags | python3 -m json.tool
```

Expected: HTTP 200 and your pulled model names.

---

## Step 6 — Configure BeatIt (local `.env` only)

Edit **local** `.env` (never commit secrets):

```env
LLM_PROVIDER=auto

# Tailscale VM
OLLAMA_BASE_URL=http://100.92.208.65:11434
OLLAMA_MODEL=qwen2.5:7b-instruct

# Fallback when VM is off (and for comparison)
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

Open http://localhost:8080 → **Settings** → confirm **Connected · ollama · qwen2.5:7b-instruct**.

Run a small custom task or baseline with a **narrow document scope** first (text-only) to validate latency.

---

## Step 8 — Production (Render) unchanged

On Render, keep:

```env
LLM_PROVIDER=openrouter
```

The VM is **not** reachable from Render unless you add Tailscale to Render (advanced). Local dev gets cheap/private LLM; production keeps OpenRouter.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Mac cannot reach `:11434` | Tailscale connected? Firewall rule? `OLLAMA_HOST=0.0.0.0`? Restart Ollama |
| `model_available: false` | Run `ollama pull` for the name in `OLLAMA_MODEL` |
| Very slow responses | Normal on CPU; use smaller model or increase VM to 8 vCPU / 32 GB RAM |
| BeatIt still uses OpenRouter | `LLM_PROVIDER=auto` and VM health must pass; check `./scripts/check_ollama.sh` |

---

## Next (this branch)

- [ ] Vision API for selected DICOM slices → `llama3.2-vision` on same VM  
- [ ] Settings UI: show Ollama URL + available models from `/api/tags`  
- [ ] Optional: bump VM RAM on host if imaging workloads grow  

---

## Branch workflow

```bash
# Work on VM integration
git checkout feature/tailscale-ollama-vm

# Fall back to stable production line
git checkout main
```

Merge to `main` only after local Ollama runs reliably end-to-end.
