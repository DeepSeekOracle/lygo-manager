#!/usr/bin/env python3
"""Music drop importer for justinhelmer.ca/listen.html.

Fixed behavior:
  folder/files -> hash + de-dupe -> 160k stream -> HF -> playlist -> boot JSON -> git.

The audio source may be MP3, WAV, FLAC or M4A. MP3 is copied as-is; other formats
are encoded to 160k MP3. Track identity is the SHA-256 of the source file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a"}
HF_REPO = "DeepSeekOracle/excavationpro-music-stream"
HF_BASE = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main"
VAULT = Path(r"<EDRIVE>\MUSIC_VAULT\public_stream")
DEFAULT_REPO = Path(r"D:\justinhelmer-ca")
DEFAULT_ARTIST = "Excavationpro"
SLIM_KEYS = ("title", "sha256", "isrcs", "aliases", "size", "stream_url", "album", "artist", "moniker")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pretty_title(stem: str) -> str:
    s = re.sub(r"-converted$", "", stem, flags=re.I)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def song_key(relpath: str) -> str:
    stem = Path(relpath).stem
    stem = re.sub(r"-converted$", "", stem, flags=re.I)
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    stem = stem.replace("_", " ")
    return re.sub(r"\s+", " ", stem).strip().lower()


def album_for(folder: Path) -> str:
    parts = [p for p in str(folder).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return "Singles"
    if parts[-1].lower() == "done" and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def ffprobe(path: Path) -> dict:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            text=True, timeout=60,
        )
        return json.loads(out)
    except Exception:
        return {}


def tags_for(path: Path) -> dict:
    info = ffprobe(path)
    tags = (info.get("format") or {}).get("tags") or {}
    return {str(k).lower(): str(v).strip() for k, v in tags.items()}


def duration(path: Path) -> float:
    info = ffprobe(path)
    try:
        return float((info.get("format") or {}).get("duration"))
    except Exception:
        return 0.0


def encode_to(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn",
           "-codec:a", "libmp3lame", "-b:a", "160k", "-ar", "44100", str(dest)]
    subprocess.run(cmd, check=True)


def collect(folder: Path) -> list[tuple[str, Path]]:
    out = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            p = Path(dirpath) / name
            if p.suffix.lower() in AUDIO_EXT:
                out.append((str(p.relative_to(folder)).replace("\\", "/"), p))
    return sorted(out)


def run_git(repo: Path, *args: str, check: bool = True,
            env: dict | None = None) -> subprocess.CompletedProcess:
    """Run git and keep its output, so a failure can say what actually happened."""
    result = subprocess.run(["git", "-C", str(repo), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=env)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {result.returncode})\n"
            + (result.stdout or "").strip())
    return result


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant,
                   check=False).returncode == 0


def _short(sha: str) -> str:
    return (sha or "?").strip()[:8]


def _conflicts(repo: Path) -> str:
    return run_git(repo, "diff", "--name-only", "--diff-filter=U",
                   check=False).stdout.strip()


def get_repo_ready(repo: Path) -> None:
    """Level the checkout with origin/main without ever discarding a commit.

    A publisher checkout can legitimately be behind, ahead, or diverged --
    another clone may have published while this one was working. Only a real
    file conflict stops the drop, and it names the files.
    """
    if not (repo / "data/public_stream_playlist.json").exists():
        raise RuntimeError(f"not a listen repo: {repo}")
    run_git(repo, "fetch", "origin")
    dirty = run_git(repo, "status", "--porcelain").stdout.strip()
    if dirty:
        raise RuntimeError("repo has uncommitted changes; refusing to publish over them\n"
                           + dirty[:1200])
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    origin = run_git(repo, "rev-parse", "origin/main").stdout.strip()
    if head == origin:
        return
    if _is_ancestor(repo, origin, head):
        ahead = run_git(repo, "rev-list", "--count", f"{origin}..{head}").stdout.strip()
        print(f"REPO: {_short(head)} is {ahead} commit(s) ahead of origin/main; keeping them")
        return
    if _is_ancestor(repo, head, origin):
        run_git(repo, "merge", "--ff-only", "origin/main")
        print(f"REPO: fast-forwarded to origin/main {_short(origin)}")
        return
    print(f"REPO: {_short(head)} and origin/main {_short(origin)} diverged; rebasing onto origin")
    rebase = run_git(repo, "rebase", "origin/main", check=False)
    if rebase.returncode != 0:
        conflicted = _conflicts(repo)
        run_git(repo, "rebase", "--abort", check=False)
        raise RuntimeError(
            "music commit conflicts with origin/main; rebase aborted, nothing published\n"
            + (conflicted or (rebase.stdout or "").strip()[-1200:]))
    if not _is_ancestor(repo, origin, run_git(repo, "rev-parse", "HEAD").stdout.strip()):
        raise RuntimeError("repo could not be reconciled with origin/main")


def push_with_recovery(repo: Path, attempts: int = 3) -> None:
    """Push; if another checkout published first, replay our commit and retry."""
    last = ""
    for attempt in range(1, attempts + 1):
        result = run_git(repo, "push", "origin", "HEAD:refs/heads/main", check=False)
        if result.returncode == 0:
            head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
            remote = run_git(repo, "ls-remote", "origin", "refs/heads/main",
                             check=False).stdout.split()
            if not remote or remote[0] != head:
                shown = _short(remote[0]) if remote else "unreadable"
                raise RuntimeError(f"push claimed success but origin/main is {shown}, not {_short(head)}")
            print(f"PUSHED origin/main = {_short(head)}")
            return
        last = (result.stdout or "").strip()
        tail = last.splitlines()[-1] if last else "push failed"
        print(f"PUSH-RECOVERY: attempt {attempt} rejected ({tail[:200]}); fetching and replaying")
        run_git(repo, "fetch", "origin")
        rebase = run_git(repo, "rebase", "origin/main", check=False)
        if rebase.returncode != 0:
            conflicted = _conflicts(repo)
            run_git(repo, "rebase", "--abort", check=False)
            raise RuntimeError(
                "music commit conflicts with origin/main; rebase aborted, nothing published\n"
                + (conflicted or (rebase.stdout or "").strip()[-1200:]))
    raise RuntimeError(f"git push failed after {attempts} attempts\n{last}")


def upload_streams(new_streams: list[Path]) -> None:
    from huggingface_hub import HfApi
    api = HfApi()
    who = api.whoami()
    print(f"HF user: {who.get('name')}")
    for p in new_streams:
        repo_path = f"stream/{p.stem[:2]}/{p.name}"
        print(f"HF {repo_path}")
        api.upload_file(path_or_fileobj=str(p), path_in_repo=repo_path,
                        repo_id=HF_REPO, repo_type="dataset",
                        commit_message=f"stream add {p.stem[:12]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--album", default="")
    ap.add_argument("--artist", default=DEFAULT_ARTIST)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--page", default="listen.html")
    ap.add_argument("--skip-hf", action="store_true")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    if not args.folder.is_dir():
        print(f"folder not found: {args.folder}"); return 2
    repo = args.repo
    get_repo_ready(repo)
    data_path = repo / "data/public_stream_playlist.json"
    page_path = repo / args.page
    if not page_path.exists():
        print(f"page not found: {page_path}"); return 2
    playlist = json.loads(data_path.read_text(encoding="utf-8"))
    tracks = playlist.get("tracks") or []
    existing = {t.get("sha256") for t in tracks if t.get("sha256")}
    files = collect(args.folder)
    if not files:
        print("no audio files found"); return 3
    print(f"audio files: {len(files)}; playlist: {len(tracks)}")

    # hash and group by song key; prefer MP3 over WAV/FLAC twin
    rows = [{"rel": rel, "path": p, "sha": sha256_file(p), "ext": p.suffix.lower()}
            for rel, p in files]
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(song_key(r["rel"]), []).append(r)

    selected, skipped = [], []
    for _key, items in sorted(groups.items()):
        mp3s = [r for r in items if r["ext"] == ".mp3"]
        chosen = mp3s[0] if mp3s else items[0]
        if chosen["sha"] in existing:
            skipped.append((chosen["rel"], "already published"))
            continue
        selected.append(chosen)
        for extra in items:
            if extra is not chosen:
                skipped.append((extra["rel"], "same-song export variant"))
    # exact duplicate hashes
    seen = set()
    unique = []
    for r in selected:
        if r["sha"] in seen:
            skipped.append((r["rel"], "duplicate in this drop"))
        else:
            seen.add(r["sha"]); unique.append(r)
    selected = unique
    print(f"new tracks: {len(selected)}; skipped: {len(skipped)}")
    for rel, why in skipped:
        print(f"  skip {rel} ({why})")
    if not selected:
        print("nothing new to add"); return 0

    entries = []
    new_streams = []
    for r in selected:
        src = r["path"]
        sha = r["sha"]
        tags = tags_for(src)
        title = tags.get("title") or pretty_title(src.stem)
        album = args.album or tags.get("album")
        if not album:
            album = "Singles" if src.parent.resolve() == args.folder.resolve() else album_for(src.parent)
        artist = tags.get("artist") or args.artist
        isrcs = []
        if tags.get("isrc"):
            isrcs = [x.strip() for x in re.split(r"[,;/ ]+", tags["isrc"]) if x.strip()]
        hf_path = f"stream/{sha[:2]}/{sha}.mp3"
        local = VAULT / sha[:2] / f"{sha}.mp3"
        local.parent.mkdir(parents=True, exist_ok=True)
        if r["ext"] == ".mp3":
            shutil.copy2(src, local)
            action = "copy-mp3"
        else:
            encode_to(src, local)
            action = "encode-160k"
        size = local.stat().st_size
        new_streams.append(local)
        entries.append({
            "sha256": sha, "title": title, "aliases": [title, pretty_title(src.stem), album],
            "isrcs": isrcs, "size": size, "stream_file": f"{sha}.mp3",
            "local_stream": str(local), "stream_url": f"{HF_BASE}/{hf_path}", "hf_path": hf_path,
            "album": album, "artist": artist, "moniker": "", "added_utc": utc_now(),
            "source": str(src), "action": action,
        })
        print(f"  {action}: {title} -> {hf_path}")

    # HF streams first, so the page never points at a missing object
    if args.skip_hf:
        print("HF upload skipped")
    else:
        upload_streams(new_streams)

    tracks.extend(entries)
    playlist["tracks"] = tracks
    playlist["generated_at"] = utc_now()
    playlist["published_at"] = utc_now()
    stats = playlist.setdefault("stats", {})
    stats["playlist_tracks"] = len(tracks)
    stats["encoded_or_ready"] = sum(1 for t in tracks if t.get("stream_url"))
    stats["remote_streams"] = stats["encoded_or_ready"]
    stats["total_stream_bytes"] = sum(int(t.get("size") or 0) for t in tracks)
    stats["total_stream_gb"] = round(stats["total_stream_bytes"] / 1_000_000_000, 2)
    stats["last_ingest"] = {"utc": utc_now(), "added_or_updated": len(entries),
                            "source": str(args.folder)}
    data_path.write_text(json.dumps(playlist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.skip_hf:
        from huggingface_hub import HfApi
        print("HF playlist")
        HfApi().upload_file(path_or_fileobj=str(data_path), path_in_repo="public_stream_playlist.json",
                            repo_id=HF_REPO, repo_type="dataset", commit_message="playlist update")

    # The site has two supported shells: an embedded boot block, or the newer
    # external listen-data.js file. Update whichever one this checkout uses.
    html = page_path.read_text(encoding="utf-8")
    m = re.search(r'(<script id="boot" type="application/json">)(.*?)(</script>)', html, re.S)
    page_changed = False
    if m:
        boot = json.loads(m.group(2))
        boot_playlist = boot.setdefault("playlist", {})
        boot_playlist["tracks"] = [{k: t.get(k) for k in SLIM_KEYS} for t in tracks]
        boot_playlist["stats"] = stats
        boot_playlist["generated_at"] = playlist["generated_at"]
        boot_json = json.dumps(boot, ensure_ascii=False, separators=(",", ":"))
        html = html[:m.start(2)] + boot_json + html[m.end(2):]
        page_path.write_text(html, encoding="utf-8", newline="")
        page_changed = True
        print(f"PLAYLIST {len(tracks)} tracks; boot injected; {len(entries)} new")
    elif (repo / "listen-data.js").exists():
        data_js = repo / "listen-data.js"
        js = data_js.read_text(encoding="utf-8")
        prefix = "window.__LISTEN_BOOT__ = "
        if not js.startswith(prefix):
            raise RuntimeError("listen-data.js has an unknown wrapper")
        boot = json.loads(js[len(prefix):].rstrip().rstrip(";"))
        boot_playlist = boot.setdefault("playlist", {})
        boot_playlist["tracks"] = [{k: t.get(k) for k in SLIM_KEYS} for t in tracks]
        boot_playlist["stats"] = stats
        boot_playlist["generated_at"] = playlist["generated_at"]
        data_js.write_text(prefix + json.dumps(boot, ensure_ascii=False, separators=(",", ":")) + ";\n",
                           encoding="utf-8", newline="")
        stamp = datetime.now().strftime("%Y%m%d%H%M")
        html2 = re.sub(r'listen-data\.js\?v=[^"\']*', f'listen-data.js?v={stamp}', html)
        if html2 != html:
            page_path.write_text(html2, encoding="utf-8", newline="")
            page_changed = True
        print(f"PLAYLIST {len(tracks)} tracks; listen-data.js rebuilt; {len(entries)} new")
    else:
        raise RuntimeError(f"{args.page} has neither boot JSON nor listen-data.js")

    if args.publish:
        add_paths = [args.page, "data/public_stream_playlist.json"]
        if (repo / "listen-data.js").exists():
            add_paths.append("listen-data.js")
        run_git(repo, "add", *add_paths)
        msg = f"Add {len(entries)} music track(s) to {args.page}"
        # Pin both author and committer: the machine-wide identity is a placeholder
        # address GitHub cannot attribute, which made music commits show up wrong.
        ident_env = os.environ.copy()
        ident_env.update({
            "GIT_AUTHOR_NAME": "DeepSeekOracle",
            "GIT_AUTHOR_EMAIL": "DeepSeekOracle@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "DeepSeekOracle",
            "GIT_COMMITTER_EMAIL": "DeepSeekOracle@users.noreply.github.com",
        })
        run_git(repo, "-c", "user.name=DeepSeekOracle",
                "-c", "user.email=DeepSeekOracle@users.noreply.github.com",
                "commit", "-m", msg, env=ident_env)
        push_with_recovery(repo)
        print("PUBLISHED")
    else:
        print("STAGED (no git push)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
