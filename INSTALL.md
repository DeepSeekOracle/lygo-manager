# Install, run and verify — LYGO Manager 1.9.1

The portal is a single Python service plus three standalone importers. There is no compiler, no
package manager step and no model to download.

## Route A — prebuilt package

1. Get **Manager 1.9.1** from the LYGO Skill Hub.
2. Verify the download hash published next to it.
3. Unpack anywhere writable (not under `Program Files` — the portal writes job output next to
   itself) and run the manager launcher.
4. Open <http://127.0.0.1:8790>.

## Route B — from source

### Prerequisites

| Need | Detail |
|---|---|
| Python | **3.13** — `C:\Python313\python.exe -V` |
| OS | Windows 10/11 for the `.bat` launcher; `python portal.py` works anywhere |
| Optional | `huggingface_hub` + `hf auth login`, only for music uploads |

### Steps

```powershell
git clone <this repo> lygo-manager
cd lygo-manager

# set the three importer paths to THIS clone
(Get-Content portal\portal.py) -replace '<SIGNAL_DIR>', (Get-Location).Path | Set-Content portal\portal.py
Select-String -Path portal\portal.py -Pattern 'IMPORTER'      # expect three real paths now

# run
C:\Python313\python.exe portal\portal.py
```

Then open <http://127.0.0.1:8790> and confirm the dashboard loads and reports a ready state.

### Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8790/api/status
C:\Python313\python.exe build\signal_drop.py --help
C:\Python313\python.exe build\music_drop.py  --help
C:\Python313\python.exe build\photo_drop.py  --help
```

Then do one **dry run**: copy a small episode folder into the drop location, let the portal
validate it, and read the built output before publishing anything real.

### Ports

| Port | Service |
|---|---|
| 8790 | Manager portal |
| 9641 | not this system — that is the LYGO LLM Console |
| 8796 | not this system — that is the Forge |

All three run side by side. If 8790 is taken, the portal will fail to bind; find the other listener
with `Get-NetTCPConnection -LocalPort 8790` rather than changing the port the dashboard expects.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Portal starts, jobs fail instantly | importer path still `<SIGNAL_DIR>` | redo the replace step; check with `Select-String` |
| `ModuleNotFoundError: huggingface_hub` | music upload branch | `pip install huggingface_hub`, or skip music uploads |
| Dashboard loads but is blank | `web/` not next to `portal.py` | keep the repo layout; the service serves `web/` relatively |
| Job stuck at "running" | one job at a time, previous still holds the lock | wait, or restart the portal |
| Publish writes to the wrong place | `publish-root`/target configured for the builder's machine | set it to your own path in the portal settings |
| Nothing publishes | by design — publishing is an explicit action | press the publish action and read the result panel |

## Uninstall

Stop the process, delete the clone, and delete any uploads or built output you created. No service,
no scheduled task, no registry entry is installed by this repo.
