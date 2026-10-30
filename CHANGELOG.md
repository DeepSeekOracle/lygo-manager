# Changelog — LYGO Manager (Signal Publish Portal)

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.9.1] — 2026-10-30 — **Public Beta 1**

### Added
- **First public source tree.** The portal and its three importers are published together for the
  first time: `portal/portal.py` with `build/signal_drop.py`, `build/music_drop.py`,
  `build/photo_drop.py`.
- **`INSTALL.md`, `DISCLAIMER.md`, `SECURITY.md`, `CHANGELOG.md`** — end-user and builder paths,
  plus the terms the portal is used under.
- **Layout note:** the repo mirrors the working layout (`portal/` beside `build/`) so the importers
  resolve exactly as they do on the steward's machine, once the `<SIGNAL_DIR>` placeholders are set.

### Changed
- **Version stamped 1.9.1** in `VERSION.md`.
- **Builder paths scrubbed.** The three absolute importer paths in `portal.py` ship as
  `<SIGNAL_DIR>\build\...` and must be pointed at your clone (one `-replace` command in
  `INSTALL.md`).

### Known at release
- The **Hugging Face upload branch** (in `music_drop.py`) is the least-exercised code path; it needs
  `huggingface_hub` and a logged-in token.
- **One job at a time**, by design.
- **Loopback only**; exposing the portal unauthenticated on a network is not supported.
- Public betas move: job schema and importer flags may change in the next beta without migration.

### Not included in this repository
`uploads/`, `music_uploads/`, `music_excav_uploads/`, `photo_uploads/` (1.1 GB of published media),
`*.bak-*` and `__pycache__`.

## Licence

LYGO Fair Usage v1.9.1 — see [`LICENSE.md`](LICENSE.md).
