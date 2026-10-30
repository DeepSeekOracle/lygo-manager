# LYGO Manager — Signal Publish Portal — v1.9.1 Public Beta

Local drop-and-publish portal for episodes, music and photo sets. Drop a fixed-format folder; a
deterministic importer validates it, builds the pages, and publishes.

**No model, no agent, no API key.** This system is deliberately LLM-free — it is a publishing
pipeline, not a chat surface.

- **Prebuilt package:** LYGO Skill Hub → Manager 1.9.1
- **From source:** [`INSTALL.md`](INSTALL.md)
- **License:** LYGO Fair Usage v1.9.1 (see [`LICENSE.md`](LICENSE.md))

This is the **1.9.1 Public Beta** — the first public beta of the working system.

---

## What it does

```
drop folder ──▶ deterministic importer ──▶ validate ──▶ build pages ──▶ publish
```

- **`portal/portal.py`** — the local service on port **8790**, loopback only, one job at a time.
- **`portal/web/`** — the dashboard (`index.html`, `app.js`, `style.css`).
- **`build/signal_drop.py`** — episode importer: `--publish` takes a fixed-format episode zip.
- **`build/music_drop.py`** — music importer (also the Hugging Face upload path).
- **`build/photo_drop.py`** — photo-set importer.

Each importer is standalone: standard library only, except `music_drop.py`, which imports
`huggingface_hub` for the upload branch.

## Requirements

- Windows 10/11 (the launcher path is Windows-specific) or any OS if you run `python portal.py`.
- Python **3.13**.
- Optional: `huggingface_hub` and a logged-in `hf` CLI, only for the music upload branch.
- Disk: the importers stage copies of what you drop. Uploads are excluded from this repository.

## Quick start

```powershell
git clone <this repo> lygo-manager
cd lygo-manager

# 1. point the three importer paths at YOUR clone (they ship as <SIGNAL_DIR> placeholders)
(Get-Content portal\portal.py) -replace '<SIGNAL_DIR>', (Get-Location).Path | Set-Content portal\portal.py

# 2. run
C:\Python313\python.exe portal\portal.py
# open http://127.0.0.1:8790
```

Full detail: [`INSTALL.md`](INSTALL.md).

## Layout

```
lygo-manager/
  portal/
    portal.py          the service (port 8790)
    web/               dashboard: index.html, app.js, style.css
  build/
    signal_drop.py     episode importer
    music_drop.py      music importer (+ HF upload)
    photo_drop.py      photo-set importer
```

`portal.py` reaches the importers by absolute path. Those three constants ship as
`<SIGNAL_DIR>\build\...` and must be set to your clone — see step 1 above. This is the one manual
step; you can also set them once in the portal UI if your build exposes the settings panel.

## What is deliberately not in this repository

| Excluded | Why |
|---|---|
| `uploads/`, `music_uploads/`, `music_excav_uploads/`, `photo_uploads/` | 1.1 GB of published media |
| `*.bak-*` | timestamped backups of `portal.py`, `app.js`, `index.html` |
| `__pycache__` | build artefact |
| stream-bot credentials | never in a repo; the bots read them from local config |

## Verify

```powershell
# service answers
Invoke-RestMethod http://127.0.0.1:8790/api/status

# importers are syntactically sound and show their help
C:\Python313\python.exe build\signal_drop.py --help
C:\Python313\python.exe build\music_drop.py --help
C:\Python313\python.exe build\photo_drop.py --help
```

A dry run on a throwaway folder is the real test: drop one episode, watch it validate, and inspect
the built output **before** pointing it at anything live.

## Known limits

- **Placeholders must be replaced** (`<SIGNAL_DIR>`) — the public tree was scrubbed of the
  builder's paths.
- **The Hugging Face upload branch is the least-exercised path.** It needs `huggingface_hub` and a
  logged-in token; everything else is local filesystem work.
- **One job at a time, by design.** The queue is intentional; do not parallelise the importers.
- **Loopback only.** Binding this to `0.0.0.0` puts an unauthenticated publisher on your network.
- **Publishing is a human decision.** Nothing in this repo posts to social, pushes to git or
  publishes to a public host without an explicit action by you.

## Documentation

| File | |
|---|---|
| [`INSTALL.md`](INSTALL.md) | install, run, verify, troubleshoot |
| [`CHANGELOG.md`](CHANGELOG.md) | what 1.9.1 changed |
| [`DISCLAIMER.md`](DISCLAIMER.md) | what you accept by running this |
| [`SECURITY.md`](SECURITY.md) | reporting, secret rules, posture |

**Steward:** Justin Helmer — Lightfather · Excavationpro · DeepSeekOracle
