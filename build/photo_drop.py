#!/usr/bin/env python3
"""Photo drop importer for the Excavationpro Data Vault gallery.

Source photos -> web full + square thumb -> gallery_manifest.json -> git.
The gallery page reads the manifest at runtime, so no page rebuild is needed.

Self-healing:
- A dirty worktree is auto-saved to a named git stash, then restored afterwards.
- A stale/non-fast-forward push is fetched, rebased, and retried.
- A failed job still restores the saved worktree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
DEFAULT_REPO = Path(r"<USER>\Documents\deepseek-harness\default-workspace\domain_signal_work")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def title_for(path: Path) -> str:
    s = re.sub(r"[-_]+", " ", path.stem)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:120] or "Untitled"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def status_porcelain(repo: Path) -> str:
    return git(repo, "status", "--porcelain").stdout.strip()


class RepoGuard:
    """Keep a dirty checkout safe and make the photo publish resilient."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.stash_message: Optional[str] = None
        self.stash_sha: Optional[str] = None

    def prepare(self) -> None:
        if not (self.repo / ".git").exists():
            raise RuntimeError(f"not a git checkout: {self.repo}")
        result = git(self.repo, "fetch", "origin", check=False)
        if result.returncode != 0:
            raise RuntimeError("git fetch failed; check network and remote access\n" + result.stdout.strip())
        dirty = status_porcelain(self.repo)
        if dirty:
            self._auto_stash()
        self._fast_forward()

    def _auto_stash(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        message = f"photo-drop auto-save {stamp} {os.getpid()}"
        before = status_porcelain(self.repo)
        result = git(self.repo, "stash", "push", "-u", "-m", message, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                "dirty worktree could not be auto-saved; no photo commit was made\n"
                + result.stdout.strip()
            )
        remaining = status_porcelain(self.repo)
        if remaining:
            # Sparse checkouts can report a path that stash refused to remove.
            # Retry once with a direct pathspec before giving up; never discard it.
            retry = git(self.repo, "stash", "push", "-u", "-m", message + " retry", check=False)
            remaining = status_porcelain(self.repo)
            if retry.returncode != 0 or remaining:
                raise RuntimeError(
                    "dirty worktree remains after auto-save; preserving it and refusing to publish\n"
                    + remaining
                )
        self.stash_message = message
        self.stash_sha = git(self.repo, "rev-parse", "refs/stash").stdout.strip()
        preview = "; ".join(before.splitlines()[:4])
        print(f"AUTO-RECOVERY: saved dirty worktree to stash ({self.stash_sha[:12]})")
        if preview:
            print(f"  saved: {preview}")

    def _fast_forward(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        origin = git(self.repo, "rev-parse", "origin/main").stdout.strip()
        if head == origin:
            return
        if git(self.repo, "merge-base", "--is-ancestor", origin, head, check=False).returncode == 0:
            print(f"REPO: {head[:8]} is ahead of origin/main; keeping local commits")
            return
        if git(self.repo, "merge-base", "--is-ancestor", head, origin, check=False).returncode == 0:
            result = git(self.repo, "merge", "--ff-only", "origin/main", check=False)
            if result.returncode != 0:
                raise RuntimeError("could not fast-forward to origin/main\n" + result.stdout.strip())
            return
        print(f"REPO: {head[:8]} and origin/main {origin[:8]} diverged; rebasing onto origin/main")
        result = git(self.repo, "rebase", "origin/main", check=False)
        if result.returncode != 0:
            conflicts = git(self.repo, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.strip()
            git(self.repo, "rebase", "--abort", check=False)
            raise RuntimeError(
                "photo commit conflicts with origin/main; rebase aborted\n"
                + (conflicts or (result.stdout or "").strip()[-1200:]))

    def restore(self) -> None:
        if not self.stash_message:
            return
        # Prefer restoring the original index state, but never leave the stash
        # unreachable if this Git version rejects --index for the sparse tree.
        result = git(self.repo, "stash", "pop", "--index", "stash@{0}", check=False)
        if result.returncode != 0:
            result = git(self.repo, "stash", "pop", "stash@{0}", check=False)
        if result.returncode == 0:
            print("AUTO-RECOVERY: restored saved worktree from stash")
        else:
            print("WARNING: saved worktree is still in git stash; nothing was discarded")
            print(result.stdout.strip())


def reconcile_with_remote(repo: Path) -> None:
    """Rebase the photo commit onto origin/main before a push retry."""
    fetch = git(repo, "fetch", "origin", check=False)
    if fetch.returncode != 0:
        raise RuntimeError("git fetch failed during push recovery\n" + fetch.stdout.strip())
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    origin = git(repo, "rev-parse", "origin/main").stdout.strip()
    if head == origin:
        return
    result = git(repo, "rebase", "origin/main", check=False)
    if result.returncode != 0:
        git(repo, "rebase", "--abort", check=False)
        raise RuntimeError("photo commit could not be rebased onto origin/main\n" + result.stdout.strip())


def commit_and_push(repo: Path, add_paths: list[str], message: str) -> None:
    git(repo, "add", "--sparse", *add_paths)
    commit = git(
        repo,
        "-c", "user.name=DeepSeekOracle",
        "-c", "user.email=DeepSeekOracle@users.noreply.github.com",
        "commit", "-m", message,
        check=False,
    )
    if commit.returncode != 0:
        raise RuntimeError("git commit failed\n" + commit.stdout.strip())
    last = ""
    for attempt in range(3):
        result = git(repo, "push", "origin", "HEAD:refs/heads/main", check=False)
        if result.returncode == 0:
            return
        last = result.stdout.strip()
        if attempt < 2:
            print(f"PUSH-RECOVERY: attempt {attempt + 1} failed; fetching/reconciling…")
            print("  " + last.splitlines()[-1][:240] if last else "  push failed")
            try:
                reconcile_with_remote(repo)
            except RuntimeError as exc:
                last = str(exc)
            time.sleep(1 + attempt)
    raise RuntimeError("git push failed after 3 attempts\n" + last)


def ensure_data_vault(repo: Path) -> tuple[Path, Path]:
    manifest = repo / "data-vault/data/gallery_manifest.json"
    gallery = repo / "data-vault/gallery.html"
    if not manifest.exists():
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "add", "data-vault/data"], check=False,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if not gallery.exists():
        raw = git(repo, "show", "HEAD:data-vault/gallery.html").stdout.encode()
        gallery.parent.mkdir(parents=True, exist_ok=True)
        gallery.write_bytes(raw)
    if not manifest.exists():
        raw = git(repo, "show", "HEAD:data-vault/data/gallery_manifest.json").stdout.encode()
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_bytes(raw)
    return manifest, gallery


def process_photos(args: argparse.Namespace, repo: Path) -> int:
    manifest_path, gallery_path = ensure_data_vault(repo)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    existing = {str(x.get("id")) for x in items}
    files = []
    for dirpath, _dirs, names in os.walk(args.folder):
        for name in names:
            p = Path(dirpath) / name
            if p.suffix.lower() in IMAGE_EXT:
                files.append(p)
    files.sort()
    if not files:
        print("no image files found"); return 3
    print(f"images: {len(files)}; manifest: {len(items)}")

    full_dir = repo / "data-vault/assets/gallery/full"
    thumb_dir = repo / "data-vault/assets/gallery/thumb"
    full_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    tags = ["PHOTO"]
    for t in re.split(r"[,;]+", args.tags or ""):
        t = t.strip().upper()
        if t and t not in tags:
            tags.append(t)

    added = []
    skipped = []
    for src in files:
        digest = sha256_file(src)
        pid = digest[:16]
        if pid in existing:
            skipped.append((src.name, "already in gallery"))
            continue
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", im.size, (10, 16, 26))
                    if im.mode == "P":
                        im = im.convert("RGBA")
                    bg.paste(im, mask=im.getchannel("A") if "A" in im.getbands() else None)
                    im = bg
                elif im.mode != "RGB":
                    im = im.convert("RGB")
                width, height = im.size
                full = im.copy()
                full.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                full_path = full_dir / f"{pid}.jpg"
                full.save(full_path, "JPEG", quality=85, optimize=True, progressive=True)
                thumb = ImageOps.fit(im, (480, 480), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
                thumb_path = thumb_dir / f"{pid}.jpg"
                thumb.save(thumb_path, "JPEG", quality=82, optimize=True, progressive=True)
            title = args.title.strip() if args.title.strip() and len(files) == 1 else title_for(src)
            rel = str(src.relative_to(args.folder)).replace("\\", "/")
            items.append({
                "id": pid, "title": title, "tags": tags,
                "source_rel": rel,
                "source_root": "Publish Portal" if src.parent.resolve() == args.folder.resolve() else args.folder.name,
                "original_name": src.name, "original_bytes": src.stat().st_size,
                "full": f"assets/gallery/full/{pid}.jpg",
                "thumb": f"assets/gallery/thumb/{pid}.jpg",
                "full_bytes": full_path.stat().st_size,
                "width": width, "height": height,
            })
            added.append((pid, title))
            print(f"  add {title} -> {pid}")
        except Exception as exc:  # noqa: BLE001
            skipped.append((src.name, f"image error: {exc}"))
    if not added:
        print(f"nothing new; skipped {len(skipped)}")
        return 0
    manifest["count"] = len(items)
    manifest["generated_utc"] = utc_now()
    manifest["cache_bust"] = f"{len(items)}-auto-{datetime.now().strftime('%Y%m%d%H%M')}"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    html = gallery_path.read_text(encoding="utf-8")
    if "<option>PHOTO</option>" not in html:
        html = html.replace("      <option>MUSIC_ART</option>", "      <option>MUSIC_ART</option>\n      <option>PHOTO</option>", 1)
    html = re.sub(r"gallery_manifest\.json\?v=[^\"']*", f"gallery_manifest.json?v={manifest['cache_bust']}", html)
    gallery_path.write_text(html, encoding="utf-8")
    print(f"manifest {len(items)}; cache {manifest['cache_bust']}")

    if args.publish:
        add_paths = ["data-vault/data/gallery_manifest.json", "data-vault/gallery.html"]
        for pid, _title in added:
            add_paths.append(f"data-vault/assets/gallery/full/{pid}.jpg")
            add_paths.append(f"data-vault/assets/gallery/thumb/{pid}.jpg")
        commit_and_push(repo, add_paths, f"Add {len(added)} photo(s) to Data Vault gallery")
        print("PUBLISHED")
    else:
        print("STAGED (no git push)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True, type=Path)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--title", default="")
    ap.add_argument("--tags", default="PHOTO")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    if not args.folder.is_dir():
        print(f"folder not found: {args.folder}"); return 2
    repo = args.repo
    guard = RepoGuard(repo)
    try:
        guard.prepare()
        return process_photos(args, repo)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        guard.restore()


if __name__ == "__main__":
    raise SystemExit(main())
