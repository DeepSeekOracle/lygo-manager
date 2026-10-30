#!/usr/bin/env python3
"""Drop-in importer for LYGO Signal radio zips.

The zip format is fixed:
  ai_radio.mp3, cover.png, show_notes.json

Usage:
  python signal_drop.py path/to/episode.zip --repo <checkout>          # stage
  python signal_drop.py path/to/episode.zip --repo <checkout> --publish # commit + push

It uses the current served episode page as the chrome/template, so it follows the
site's hand-edited player and rail instead of the stale generator.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import email.utils
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from bs4 import BeautifulSoup

# Windows console/pipe safety: episode titles legitimately contain Δ/Δ9.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

SITE = "https://excavationpro.ca"
DEFAULT_REPO = Path(r"<USER>\Documents\deepseek-harness\default-workspace\domain_signal_work")
TEMPLATE_SLUG = "harmonizing-ai-through-bond-963f"
SPEAKER_STYLES = {
    0: "color:#5eead4;border-color:#5eead455;background:#5eead41a",
    1: "color:#fbbf24;border-color:#fbbf2455;background:#fbbf241a",
    2: "color:#93c5fd;border-color:#93c5fd55;background:#93c5fd1a",
    3: "color:#c4b5fd;border-color:#c4b5fd55;background:#c4b5fd1a",
    4: "color:#f9a8d4;border-color:#f9a8d455;background:#f9a8d41a",
    5: "color:#86efac;border-color:#86efac55;background:#86efac1a",
}


def clean(text: str) -> str:
    return (text or "").replace("\ufffd", "Þ").strip()


def slugify(name: str) -> str:
    name = Path(name).stem.lower()
    name = re.sub(r"-show$", "", name)
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return name


def mmss(seconds: float) -> str:
    n = int(float(seconds))
    return f"{n // 60}:{n % 60:02d}"


def tc_seconds(tc: str) -> int:
    parts = [int(p) for p in str(tc).split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0


def chapter_label(text: str) -> str:
    text = re.sub(r"\s+", " ", clean(text))
    text = re.sub(r"\bAI Talk Radio\b", "AI Radio", text, flags=re.I)
    # First sentence is the least arbitrary deterministic label from the transcript.
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    first = re.sub(r"^(Welcome (back )?to AI Radio[.!]?\s*)", "", first, flags=re.I)
    if len(first) > 92:
        first = first[:89].rsplit(" ", 1)[0] + "…"
    return first or "Transcript"


def probe_duration(path: Path, fallback: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            text=True, timeout=20,
        ).strip()
        return float(out)
    except Exception:
        try:
            parts = [int(x) for x in fallback.split(":")]
            return parts[0] * 60 + parts[1] if len(parts) == 2 else 0
        except Exception:
            return 0


def text_sub(tree: BeautifulSoup, old: str, new: str) -> None:
    if not old:
        return
    for node in list(tree.find_all(string=True)):
        if old in node:
            node.replace_with(node.replace(old, new))


def attr_sub(tree: BeautifulSoup, old: str, new: str) -> None:
    if not old:
        return
    for tag in tree.find_all(True):
        for key, value in list(tag.attrs.items()):
            if isinstance(value, str) and old in value:
                tag[key] = value.replace(old, new)
            elif isinstance(value, list):
                tag[key] = [v.replace(old, new) if isinstance(v, str) else v for v in value]


def set_meta(soup: BeautifulSoup, *, title: str, summary: str, slug: str, date: str,
             duration: str, audio: str, size: str) -> None:
    page = f"{SITE}/signal/{slug}/"
    image = f"{SITE}/signal/shows/{slug}/cover.png"
    names = {
        "description": summary,
        "keywords": f"{title}, AI Radio, podcast, LYGO Signal, {', '.join(title.split()[:6])}",
    }
    props = {
        "og:title": f"{title}. LYGO Signal, AI Radio",
        "og:description": summary,
        "og:url": page,
        "og:image": image,
        "og:image:secure_url": image,
        "og:image:alt": f"{title}. LYGO Signal, AI Radio, {duration}",
        "og:audio": audio,
        "twitter:title": f"{title}. LYGO Signal, AI Radio",
        "twitter:description": summary,
        "twitter:image": image,
        "twitter:image:alt": f"{title}. LYGO Signal, AI Radio, {duration}",
    }
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property")
        if key in names:
            tag["content"] = names[key]
        elif key in props:
            tag["content"] = props[key]
    canonical = soup.find("link", rel="canonical")
    if canonical:
        canonical["href"] = page
    if soup.title:
        soup.title.string = f"{title} | LYGO Signal"


def build_page(repo: Path, cfg: dict, rows: list[dict]) -> str:
    slug = cfg["slug"]
    template = (repo / "signal" / TEMPLATE_SLUG / "index.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(template, "html.parser")
    old_summary = None
    for tag in soup.find_all("meta"):
        if (tag.get("name") == "description"):
            old_summary = tag.get("content")
            break
    attr_sub(soup, TEMPLATE_SLUG, slug)
    text_sub(soup, "Harmonizing AI Through BOND_963F", cfg["title"])
    if old_summary:
        text_sub(soup, old_summary, cfg["summary"])
        attr_sub(soup, old_summary, cfg["summary"])
    text_sub(soup, "13:26", cfg["duration"])
    attr_sub(soup, "13:26", cfg["duration"])
    text_sub(soup, "18.5 MB", cfg["size"])
    attr_sub(soup, "18.5 MB", cfg["size"])
    text_sub(soup, "2026-10-21", cfg["date"])
    attr_sub(soup, "2026-10-21", cfg["date"])
    text_sub(soup, "PT13M26S", cfg["pt_duration"])
    attr_sub(soup, "PT13M26S", cfg["pt_duration"])
    set_meta(soup, title=cfg["title"], summary=cfg["summary"], slug=slug,
             date=cfg["date"], duration=cfg["duration"],
             audio=f"{SITE}/signal/shows/{slug}/ai_radio.mp3", size=cfg["size"])

    h1 = soup.find("h1")
    if h1:
        h1.string = cfg["title"]
    lead = soup.select_one(".sig-lead")
    if lead:
        lead.string = cfg["summary"]
    # chapter list
    chap = soup.select_one(".sig-chapters ol")
    if chap:
        chap.clear()
        for i, row in enumerate(rows):
            li = soup.new_tag("li")
            btn = soup.new_tag("button", attrs={"type": "button", "class": "sig-chapter",
                "data-sig-jump": str(tc_seconds(row["timecode"])), "data-player": slug})
            tc = soup.new_tag("span", attrs={"class": "sig-ch-tc"})
            tc.string = row["timecode"]
            btn.append(tc)
            btn.append(chapter_label(row["text"]))
            li.append(btn)
            chap.append(li)
    # transcript body (keep the section header)
    trans = soup.select_one("#transcript")
    if trans:
        for old in trans.select(".turn"):
            old.decompose()
        for i, row in enumerate(rows):
            sec = tc_seconds(row["timecode"])
            turn = soup.new_tag("div", attrs={"class": "turn", "id": f"turn-{i}"})
            head = soup.new_tag("p", attrs={"class": "turn-head"})
            b = soup.new_tag("button", attrs={"type": "button", "class": "tc",
                "data-sig-jump": str(sec), "data-player": slug,
                "title": f"Play from {row['timecode']}"})
            b.string = row["timecode"]
            head.append(b)
            head.append(" ")
            spk = soup.new_tag("span", attrs={"class": "spk", "style": row["style"]})
            spk.string = row["speaker"]
            head.append(spk)
            turn.append(head)
            body = soup.new_tag("p", attrs={"class": "turn-text"})
            body.string = clean(row["text"])
            turn.append(body)
            trans.append(turn)
    # speaker list
    spk_list = soup.select_one(".spk-list")
    if spk_list:
        spk_list.clear()
        seen = set()
        for row in rows:
            key = row["speaker"]
            if key in seen:
                continue
            seen.add(key)
            li = soup.new_tag("li")
            li["data-spk"] = key
            sp = soup.new_tag("span", attrs={"class": "spk", "style": row["style"]})
            sp.string = key
            li.append(sp)
            li.append(" Performed character")
            spk_list.append(li)
    # rail
    rail = soup.select_one(".sig-next")
    if rail:
        rail.replace_with(make_rail(soup, cfg, rows))
    # page JSON-LD
    script = soup.find("script", type="application/ld+json")
    if script and script.string:
        try:
            schema = json.loads(script.string)
            for node in schema.get("@graph", []):
                if node.get("@type") == "PodcastEpisode":
                    node["@id"] = f"{SITE}/signal/{slug}/#episode"
                    node["url"] = f"{SITE}/signal/{slug}/"
                    node["name"] = cfg["title"]
                    node["description"] = cfg["summary"]
                    node["datePublished"] = cfg["date"]
                    node["duration"] = cfg["pt_duration"]
                    node["image"] = f"{SITE}/signal/shows/{slug}/cover.png"
                    media = node.get("associatedMedia") or {}
                    media["contentUrl"] = f"{SITE}/signal/shows/{slug}/ai_radio.mp3"
                    media["contentSize"] = str(cfg["size_bytes"])
                    media["duration"] = cfg["pt_duration"]
                    node["associatedMedia"] = media
            script.string = json.dumps(schema, ensure_ascii=False, indent=1)
        except Exception:
            pass
    return str(soup)


def make_rail(soup: BeautifulSoup, cfg: dict, rows: list[dict]) -> object:
    div = soup.new_tag("div", attrs={"class": "sig-next"})
    h = soup.new_tag("h3"); h.string = "More from Signal"; div.append(h)
    ul = soup.new_tag("ul", attrs={"class": "sig-list"})
    for x in cfg["other_shows"]:
        li = soup.new_tag("li")
        a = soup.new_tag("a", href=f"/signal/{x['slug']}/"); a.string = x["title"]
        li.append(a)
        li.append(f", {x.get('duration','')}, {x.get('desc','')}")
        ul.append(li)
    div.append(ul)
    p = soup.new_tag("p")
    a = soup.new_tag("a", href="/signal/"); a.string = "All episodes, the feed, and the rest of the network —"
    p.append(a); div.append(p)
    return div


def make_card(soup: BeautifulSoup, cfg: dict) -> object:
    # Build from the same card shape as the served hub.
    card = soup.new_tag("article", attrs={"class": "gcard"})
    stage = soup.new_tag("div", attrs={"class": "gmod-stage"})
    a = soup.new_tag("a", href=f"/signal/{cfg['slug']}/", attrs={"aria-label": "Open the episode page"})
    img = soup.new_tag("img", attrs={"class": "gcard-cover",
        "src": f"/signal/shows/{cfg['slug']}/cover.png",
        "alt": f"Artwork for {cfg['title']}", "width": "440", "height": "440", "loading": "lazy"})
    a.append(img); stage.append(a); card.append(stage)
    body = soup.new_tag("div", attrs={"class": "gcard-body"})
    kick = soup.new_tag("p", attrs={"class": "gcard-kicker"})
    kick.append(f"AI Radio · {cfg['date']} ")
    flag = soup.new_tag("span", attrs={"class": "sig-flag"}); flag.string = "New"
    kick.append(flag); body.append(kick)
    h2 = soup.new_tag("h2"); a2 = soup.new_tag("a", href=f"/signal/{cfg['slug']}/"); a2.string = cfg["title"]; h2.append(a2); body.append(h2)
    tag = soup.new_tag("p", attrs={"class": "sig-tag"}); tag.string = cfg["summary"]; body.append(tag)
    pl = soup.new_tag("div", attrs={"class": "sig-player"})
    au = soup.new_tag("audio", attrs={"controls": "", "preload": "metadata",
        "src": f"/signal/shows/{cfg['slug']}/ai_radio.mp3", "style": "width:100%"})
    pl.append(au); body.append(pl)
    meta = soup.new_tag("p", attrs={"class": "gcard-meta"})
    meta.string = f"{cfg['duration']} · {', '.join(cfg['speakers'])} · {cfg['size']}"
    body.append(meta)
    cta = soup.new_tag("p", attrs={"class": "cta-row"})
    a3 = soup.new_tag("a", attrs={"class": "btn primary", "href": f"/signal/{cfg['slug']}/"})
    a3.string = "Episode page & transcript"
    a4 = soup.new_tag("a", attrs={"class": "btn", "href": f"/signal/shows/{cfg['slug']}/ai_radio.mp3", "download": ""})
    a4.string = "Download"
    cta.append(a3); cta.append(" "); cta.append(a4); body.append(cta)
    card.append(body)
    return card


def update_hub(repo: Path, cfg: dict, shows: list[dict]) -> None:
    p = repo / "signal/index.html"
    soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
    # remove old New flags, then prepend new card
    for flag in soup.select(".gcard .sig-flag"):
        flag.decompose()
    grid = soup.select_one(".ggrid")
    if not grid:
        raise RuntimeError("hub .ggrid not found")
    grid.insert(0, make_card(soup, cfg))
    total = int(sum(x.get("seconds", 0) for x in shows))
    runtime = f"{total // 60}:{total % 60:02d}"
    for stat in soup.select(".stat"):
        label = stat.select_one("span")
        b = stat.find("b")
        if not label or not b:
            continue
        if label.get_text(strip=True) == "episodes":
            b.string = str(len(shows))
        elif label.get_text(strip=True) == "total runtime":
            b.string = runtime
    n = str(len(shows))
    for meta in soup.find_all("meta"):
        if meta.get("content"):
            meta["content"] = re.sub(r"\b\d+ episodes free\b", f"{n} episodes free", meta["content"])
    # JSON-LD lists
    script = soup.find("script", type="application/ld+json")
    if script and script.string:
        schema = json.loads(script.string)
        radio = [x for x in shows if not x.get("desk")]
        for node in schema.get("@graph", []):
            if node.get("@type") == "PodcastSeries":
                ids = [e.get("@id") for e in node.get("episode", []) if e.get("@id")]
                new_id = f"{SITE}/signal/{cfg['slug']}/#episode"
                if new_id not in ids:
                    ids.insert(0, new_id)
                node["episode"] = [{"@id": u} for u in ids]
            if node.get("@id", "").endswith("#episodes"):
                node["numberOfItems"] = len(radio)
                node["itemListElement"] = [
                    {"@type": "ListItem", "position": i + 1,
                     "url": f"{SITE}/signal/{x['slug']}/", "name": x["title"]}
                    for i, x in enumerate(radio)
                ]
        script.string = json.dumps(schema, ensure_ascii=False, indent=1)
    p.write_text(str(soup), encoding="utf-8", newline="\n")


def update_rails(repo: Path, cfg: dict, shows: list[dict]) -> None:
    radio = [x for x in shows if not x.get("desk")]
    new_li = (f'<li><a href="/signal/{cfg["slug"]}/">{html.escape(cfg["title"])}</a>, '
              f'{cfg["duration"]}, {html.escape(cfg["summary"])}</li>')
    for page in (repo / "signal").glob("*/index.html"):
        if page.parent.name in ("brand", "radio", cfg["slug"]):
            continue
        s = page.read_text(encoding="utf-8")
        if 'class="sig-next"' in s:
            if f"/signal/{cfg['slug']}/" not in s:
                s = s.replace('<ul class="sig-list">', '<ul class="sig-list">' + new_li, 1)
                page.write_text(s, encoding="utf-8", newline="")
        else:
            # put a complete rail just before the main close
            items = []
            for x in radio:
                if x["slug"] == page.parent.name:
                    continue
                items.append(f'<li><a href="/signal/{x["slug"]}/">{html.escape(x["title"])}</a>, '
                             f'{x.get("duration","")}, {html.escape(x.get("desc", ""))}</li>')
            block = ('<div class="sig-next">\n<h3>More from Signal</h3>\n<ul class="sig-list">'
                     + "".join(items) + '</ul>\n<p><a href="/signal/">All episodes, the feed, and the rest of the network —</a></p>\n</div>\n')
            if "</main>" in s:
                s = s.replace("</main>", block + "</main>", 1)
                page.write_text(s, encoding="utf-8", newline="")


def update_radio(repo: Path, cfg: dict) -> None:
    p = repo / "signal/radio/index.html"
    if not p.exists():
        return
    soup = BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser")
    rail = soup.select_one(".hub-rail")
    if not rail:
        return
    if rail.select_one(f'a[href="/signal/{cfg["slug"]}/"]'):
        return
    a = soup.new_tag("a", href=f"/signal/{cfg['slug']}/")
    b = soup.new_tag("b"); b.string = cfg["title"]; a.append(b)
    span = soup.new_tag("span"); span.string = cfg["summary"][:160]; a.append(span)
    rail.insert(0, a)
    p.write_text(str(soup), encoding="utf-8", newline="\n")


def update_feed(repo: Path, cfg: dict) -> None:
    p = repo / "signal/feed.xml"
    s = p.read_text(encoding="utf-8")
    item = f'''  <item>
    <title>{xml_escape(cfg["title"])}</title>
    <link>{SITE}/signal/{cfg["slug"]}/</link>
    <guid isPermaLink="true">{SITE}/signal/{cfg["slug"]}/</guid>
    <pubDate>{cfg["pubdate"]}</pubDate>
    <description>{xml_escape(cfg["summary"])}</description>
    <itunes:duration>{cfg["duration"]}</itunes:duration>
    <itunes:image href="{SITE}/signal/shows/{cfg["slug"]}/cover.png"/>
    <enclosure url="{SITE}/signal/shows/{cfg["slug"]}/ai_radio.mp3" length="{cfg["size_bytes"]}" type="audio/mpeg"/>
  </item>
'''
    if f"/signal/{cfg['slug']}/" not in s:
        s = s.replace("<channel>\n", "<channel>\n" + item, 1)
    p.write_text(s, encoding="utf-8", newline="\n")


def update_sitemap(repo: Path, cfg: dict) -> None:
    p = repo / "sitemap.xml"
    s = p.read_text(encoding="utf-8")
    loc = f"{SITE}/signal/{cfg['slug']}/"
    if loc not in s:
        line = f'  <url><loc>{loc}</loc><lastmod>{cfg["date"]}</lastmod><changefreq>monthly</changefreq><priority>0.9</priority></url>\n'
        s = s.replace("</urlset>", line + "</urlset>", 1)
    p.write_text(s, encoding="utf-8", newline="\n")


def load_shows(repo: Path) -> list[dict]:
    return json.loads((repo / "signal/shows.json").read_text(encoding="utf-8"))["shows"]


def write_shows(repo: Path, shows: list[dict]) -> None:
    p = repo / "signal/shows.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["updated"] = shows[0]["date"] if shows and "date" in shows[0] else dt.date.today().isoformat()
    data["shows"] = shows
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


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


def push_with_recovery(repo: Path, attempts: int = 3) -> None:
    """Push; if another checkout published first, replay our commit and retry."""
    last = ""
    for attempt in range(1, attempts + 1):
        result = git(repo, "push", "origin", "HEAD:refs/heads/main", check=False)
        if result.returncode == 0:
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            remote = git(repo, "ls-remote", "origin", "refs/heads/main",
                         check=False).stdout.split()
            if not remote or remote[0] != head:
                shown = remote[0][:8] if remote else "unreadable"
                raise RuntimeError(f"push claimed success but origin/main is {shown}, not {head[:8]}")
            print(f"PUSHED origin/main = {head[:8]}")
            return
        last = (result.stdout or "").strip()
        tail = last.splitlines()[-1] if last else "push failed"
        print(f"PUSH-RECOVERY: attempt {attempt} rejected ({tail[:200]}); fetching and replaying")
        git(repo, "fetch", "origin", check=False)
        rebase = git(repo, "rebase", "origin/main", check=False)
        if rebase.returncode != 0:
            conflicts = git(repo, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.strip()
            git(repo, "rebase", "--abort", check=False)
            raise RuntimeError(
                "Signal commit conflicts with origin/main; rebase aborted\n"
                + (conflicts or (rebase.stdout or "").strip()[-1200:]))
    raise RuntimeError(f"git push failed after {attempts} attempts\n{last}")


def _signal_path_dirty(status: str) -> str:
    """Only Signal-owned paths block a drop. Leftover city/photo junk does not."""
    lines = []
    for line in status.splitlines():
        path = line[3:].strip().strip('"').replace("\\", "/")
        if path == "sitemap.xml" or path.startswith("signal/"):
            lines.append(line)
    return "\n".join(lines)


class RepoGuard:
    """Fetch, stash unrelated dirt, fast-forward. Restore after the drop."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.stash_message: str | None = None

    def prepare(self) -> None:
        if not (self.repo / ".git").exists() and not (self.repo / ".git").is_file():
            raise RuntimeError(f"not a git checkout: {self.repo}")
        result = git(self.repo, "fetch", "origin", check=False)
        if result.returncode != 0:
            raise RuntimeError("git fetch failed; check network and remote access\n" + (result.stdout or "").strip())
        dirty = status_porcelain(self.repo)
        blocking = _signal_path_dirty(dirty)
        if blocking:
            raise RuntimeError(
                "Signal paths are dirty. Finish or stash that Signal work first:\n" + blocking
            )
        if dirty:
            self._auto_stash()
        self._fast_forward()

    def _auto_stash(self) -> None:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        message = f"signal-drop auto-save {stamp} {os.getpid()}"
        before = status_porcelain(self.repo)
        result = git(self.repo, "stash", "push", "-u", "-m", message, check=False)
        remaining = status_porcelain(self.repo)
        if result.returncode != 0 or remaining:
            raise RuntimeError(
                "dirty worktree could not be auto-saved; no Signal commit was made\n"
                + ((result.stdout or "").strip() or remaining)
            )
        self.stash_message = message
        preview = "; ".join(before.splitlines()[:4])
        print("AUTO-RECOVERY: saved unrelated dirty files to stash")
        if preview:
            print("  saved: " + preview)

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
                raise RuntimeError("could not fast-forward to origin/main\n" + (result.stdout or "").strip())
            return
        print(f"REPO: {head[:8]} and origin/main {origin[:8]} diverged; rebasing onto origin/main")
        result = git(self.repo, "rebase", "origin/main", check=False)
        if result.returncode != 0:
            conflicts = git(self.repo, "diff", "--name-only", "--diff-filter=U",
                            check=False).stdout.strip()
            git(self.repo, "rebase", "--abort", check=False)
            raise RuntimeError(
                "Signal work conflicts with origin/main; rebase aborted\n"
                + (conflicts or (result.stdout or "").strip()[-1200:]))

    def restore(self) -> None:
        if not self.stash_message:
            return
        result = git(self.repo, "stash", "pop", "stash@{0}", check=False)
        if result.returncode == 0:
            print("AUTO-RECOVERY: restored saved worktree from stash")
        else:
            print("WARNING: saved worktree is still in git stash; nothing was discarded")
            print((result.stdout or "").strip())


def process_zip(zip_path: Path, repo: Path, publish: bool) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="lygo-signal-drop-"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        notes_p = tmp / "show_notes.json"
        audio_p = tmp / "ai_radio.mp3"
        cover_p = tmp / "cover.png"
        for p in (notes_p, audio_p, cover_p):
            if not p.exists():
                raise RuntimeError(f"missing {p.name} in {zip_path.name}")
        notes = json.loads(notes_p.read_text(encoding="utf-8"))
        title = clean(notes["show_title"])
        slug = slugify(zip_path.name)
        rows_in = notes.get("timecoded_transcript") or []
        if not rows_in:
            raise RuntimeError("no timecoded_transcript")
        duration_s = probe_duration(audio_p, str(notes.get("show_duration") or "0:00"))
        if duration_s <= 0:
            raise RuntimeError("could not measure audio duration")
        duration = mmss(duration_s)
        size_bytes = audio_p.stat().st_size
        size = f"{size_bytes / 1_000_000:.1f} MB"
        date = str(notes.get("date_of_generation") or dt.date.today().isoformat())[:10]
        summary = clean(notes.get("two_sentence_summary") or "")
        if not summary:
            summary = f"{title} — AI Radio."
        speakers = []
        for r in rows_in:
            if r["speaker"] not in speakers:
                speakers.append(r["speaker"])
        styles = {s: SPEAKER_STYLES[i % len(SPEAKER_STYLES)] for i, s in enumerate(speakers)}
        rows = [{"speaker": r["speaker"], "text": r["text"], "timecode": r["timecode"],
                 "style": styles[r["speaker"]]} for r in rows_in]
        shows = load_shows(repo)
        if any(x["slug"] == slug for x in shows):
            raise RuntimeError(f"slug already exists: {slug}; refusing duplicate")
        desc = {x["slug"]: x.get("desc", x["title"]) for x in shows}
        # target uses title as a fallback when there is no tagline
        pubdate = email.utils.format_datetime(
            dt.datetime.fromisoformat(date).replace(hour=12, tzinfo=dt.timezone.utc)
        )
        cfg = {"slug": slug, "title": title, "summary": summary, "date": date,
               "pubdate": pubdate,
               "duration": duration, "seconds": round(duration_s, 2), "size": size,
               "size_bytes": size_bytes, "speakers": speakers,
               "pt_duration": f"PT{int(duration_s)//60}M{int(duration_s)%60}S",
               "other_shows": [{"slug": x["slug"], "title": x["title"], "duration": x.get("duration", ""),
                                "desc": desc.get(x["slug"], x["title"])} for x in shows if not x.get("desk")]}
        # copy assets
        out = repo / "signal/shows" / slug
        out.mkdir(parents=True, exist_ok=True)
        for p in (audio_p, cover_p, notes_p):
            shutil.copy2(p, out / p.name)
        # insert show at top
        new_row = {"slug": slug, "title": title,
                   "page": f"{SITE}/signal/{slug}/",
                   "audio": f"{SITE}/signal/shows/{slug}/ai_radio.mp3",
                   "duration": duration, "seconds": round(duration_s, 2),
                   "speakers": speakers, "date": date}
        shows.insert(0, new_row)
        cfg["other_shows"] = [{"slug": x["slug"], "title": x["title"], "duration": x.get("duration", ""),
                               "desc": desc.get(x["slug"], x["title"])} for x in shows if not x.get("desk") and x["slug"] != slug]
        page = build_page(repo, cfg, rows)
        page_dir = repo / "signal" / slug
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "index.html").write_text(page, encoding="utf-8", newline="\n")
        update_hub(repo, cfg, shows)
        update_rails(repo, cfg, shows)
        update_feed(repo, cfg)
        update_radio(repo, cfg)
        update_sitemap(repo, cfg)
        write_shows(repo, shows)
        # structural validation
        from bs4 import BeautifulSoup as BS
        out_soup = BS((page_dir / "index.html").read_text(encoding="utf-8"), "html.parser")
        if len(out_soup.select(".sig-chapter")) != len(rows):
            raise RuntimeError("chapter count mismatch")
        if len(out_soup.select(".turn")) != len(rows):
            raise RuntimeError("transcript count mismatch")
        if len(out_soup.select(".sig-next li")) != len(cfg["other_shows"]):
            raise RuntimeError("rail count mismatch")
        hub = BS((repo / "signal/index.html").read_text(encoding="utf-8"), "html.parser")
        if len(hub.select(".gcard")) < len(shows):
            raise RuntimeError("hub card count mismatch")
        print(f"STAGED {slug}: {title} | {duration} | {size} | {len(rows)} turns")
        print(f"SHOWS={len(shows)} RADIO={len([x for x in shows if not x.get('desk')])}")
        if publish:
            subprocess.run(["git", "-C", str(repo), "add", "signal", "sitemap.xml"], check=True)
            msg = f"Add {title} to Signal"
            env = os.environ.copy()
            env.setdefault("GIT_AUTHOR_NAME", "DeepSeekOracle")
            env.setdefault("GIT_AUTHOR_EMAIL", "DeepSeekOracle@users.noreply.github.com")
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=DeepSeekOracle",
                            "-c", "user.email=DeepSeekOracle@users.noreply.github.com",
                            "commit", "-m", msg], check=True, env=env)
            push_with_recovery(repo)
            print("PUBLISHED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", nargs="*", type=Path)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()
    zips = args.zip or list(Path(r"<DESKTOP>\LYGO Signal Drop\INBOX").glob("*.zip"))
    if not zips:
        print("No zips. Put one in the INBOX or pass a zip path.")
        return 2
    repo = args.repo
    guard = RepoGuard(repo)
    try:
        guard.prepare()
    except RuntimeError as exc:
        print(exc)
        return 3
    try:
        for z in zips:
            process_zip(z, repo, args.publish)
    except Exception as exc:  # noqa: BLE001 — portal needs the traceback in the job log
        print("ERROR:", exc)
        return 1
    finally:
        guard.restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
