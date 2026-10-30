#!/usr/bin/env python3
"""Local LYGO Signal publish portal.

Drop a fixed-format episode zip; the portal runs signal_drop.py --publish and
streams the result. Bind is loopback only. One job is processed at a time.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import os
import platform
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import psutil
except ImportError:  # dashboard still starts; system panels report unavailable
    psutil = None
try:
    import requests
except ImportError:
    requests = None

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
REPO_DEFAULT = Path(os.environ.get("SIGNAL_REPO", r"<USER>\Documents\deepseek-harness\default-workspace\domain_signal_work"))
IMPORTER = Path(r"<SIGNAL_DIR>\build\signal_drop.py")
MUSIC_IMPORTER = Path(r"<SIGNAL_DIR>\build\music_drop.py")
MUSIC_REPO = Path(os.environ.get("MUSIC_REPO", r"D:\justinhelmer-ca"))
MUSIC_PUBLISH = os.environ.get("MUSIC_PORTAL_PUBLISH", os.environ.get("SIGNAL_PORTAL_PUBLISH", "1")).strip().lower() not in ("0", "false", "no")
MUSIC_SKIP_HF = os.environ.get("MUSIC_SKIP_HF", "0").strip().lower() in ("1", "true", "yes")
MUSIC_EXCAV_REPO = Path(os.environ.get("MUSIC_EXCAV_REPO", r"D:\excavationpro-ca"))
MUSIC_EXCAV_PUBLISH = os.environ.get("MUSIC_EXCAV_PUBLISH", os.environ.get("MUSIC_PORTAL_PUBLISH", "1")).strip().lower() not in ("0", "false", "no")
MUSIC_EXCAV_SKIP_HF = os.environ.get("MUSIC_EXCAV_SKIP_HF", os.environ.get("MUSIC_SKIP_HF", "0")).strip().lower() in ("1", "true", "yes")
MUSIC_EXCAV_UPLOADS = HERE / "music_excav_uploads"
MUSIC_UPLOADS = HERE / "music_uploads"
PHOTO_IMPORTER = Path(r"<SIGNAL_DIR>\build\photo_drop.py")
PHOTO_REPO = Path(os.environ.get("PHOTO_REPO", r"<USER>\Documents\deepseek-harness\default-workspace\domain_signal_work"))
PHOTO_PUBLISH = os.environ.get("PHOTO_PORTAL_PUBLISH", os.environ.get("SIGNAL_PORTAL_PUBLISH", "1")).strip().lower() not in ("0", "false", "no")
PHOTO_UPLOADS = HERE / "photo_uploads"
UPLOADS = HERE / "uploads"
PORT = int(os.environ.get("SIGNAL_PORTAL_PORT", "8790"))
PUBLISH = os.environ.get("SIGNAL_PORTAL_PUBLISH", "1").strip().lower() not in ("0", "false", "no")
SITE = "https://excavationpro.ca"
MAX_UPLOAD = 120 * 1024 * 1024
GENESIS_WALLET = os.environ.get("GENESIS_WALLET", "0x0814209fc50866C38186537Cd7C534060E011Ec5")
GENESIS_RPC = os.environ.get("GENESIS_RPC", "https://mainnet.base.org")
GENESIS_ROOT = Path(os.environ.get("GENESIS_ROOT", r"<USER>\OLDopenclaw\Old files openclaw\OLD openclaw\workspace"))
GENESIS_AUDIT = Path(os.environ.get("GENESIS_AUDIT") or (GENESIS_ROOT / "audit_logs/Audit_Report.json"))
JOBS: dict[str, dict] = {}
JOB_ORDER: list[str] = []
Q: queue.Queue[str] = queue.Queue()
LOCK = threading.Lock()
_GENESIS_CACHE: dict = {"at": 0.0, "data": None}
_GENESIS_LOCK = threading.Lock()


def log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)


def slugify(name: str) -> str:
    name = Path(name).stem.lower()
    name = re.sub(r"-show$", "", name)
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def clean_old_uploads() -> None:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 14 * 86400
    for p in UPLOADS.rglob("*.zip"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    for d in UPLOADS.iterdir():
        if d.is_dir():
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass


def worker() -> None:
    while True:
        jid = Q.get()
        job = JOBS.get(jid)
        if not job:
            Q.task_done(); continue
        job["status"] = "running"
        job["started"] = dt.datetime.now().isoformat(timespec="seconds")
        job["log"].append(f"Starting {job['filename']}")
        if job.get("kind") == "photo":
            cmd = [sys.executable, str(PHOTO_IMPORTER), "--folder", job["path"],
                   "--repo", str(PHOTO_REPO), "--title", job.get("title", ""),
                   "--tags", job.get("tags", "PHOTO")]
            if job.get("publish"):
                cmd.append("--publish")
        elif job.get("kind") == "music":
            cmd = [sys.executable, str(MUSIC_IMPORTER), "--folder", job["path"],
                   "--album", job.get("album", ""), "--artist", job.get("artist", "Excavationpro"),
                   "--repo", str(MUSIC_REPO)]
            if job.get("publish"):
                cmd.append("--publish")
            if MUSIC_SKIP_HF:
                cmd.append("--skip-hf")
        elif job.get("kind") == "music-excav":
            cmd = [sys.executable, str(MUSIC_IMPORTER), "--folder", job["path"],
                   "--album", job.get("album", ""), "--artist", job.get("artist", "Excavationpro"),
                   "--repo", str(MUSIC_EXCAV_REPO), "--page", "music.html"]
            if job.get("publish"):
                cmd.append("--publish")
            if MUSIC_EXCAV_SKIP_HF:
                cmd.append("--skip-hf")
        else:
            cmd = [sys.executable, str(IMPORTER), job["path"], "--repo", job["repo"]]
            if job["publish"]:
                cmd.append("--publish")
        job["log"].append("$ " + " ".join(cmd))
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    job["log"].append(line)
                    log(line)
            rc = proc.wait()
            job["returncode"] = rc
            if rc == 0:
                job["status"] = "published" if job["publish"] else "staged"
                if job.get("kind") not in ("music", "music-excav"):
                    job["url"] = f"{SITE}/signal/{job['slug']}/"
                job["log"].append("DONE")
            else:
                job["status"] = "error"
                job["error"] = f"importer exited {rc}"
                job["log"].append(f"ERROR: importer exited {rc}")
        except Exception as exc:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = str(exc)
            job["log"].append(f"ERROR: {exc}")
        finally:
            job["finished"] = dt.datetime.now().isoformat(timespec="seconds")
            Q.task_done()


COMMANDER_BOOTS = [
    ("hermes-paid", "Hermes · DeepSeek Paid", r"<DESKTOP>\AGENT STUDIO\Hermes DeepSeek Paid.bat", "AGENTS", "DeepSeek paid agent console"),
    ("hermes-qwen", "Hermes · QwenPaw 9B (retired)", r"<DESKTOP>\AGENT STUDIO\Hermes Local QwenPaw 9B.bat", "AGENTS", "Local QwenPaw agent console"),
    ("grok", "Start Grok", r"<DESKTOP>\AGENT STUDIO\Start-Grok.bat", "AGENTS", "Grok agent launcher"),
    ("deepseek-harness", "DeepSeek Harness", r"<DESKTOP>\AGENT STUDIO\DeepSeek Harness.lnk", "AGENTS", "DSH desktop app", "dsh"),
    ("agent-console", "LYGO Local Agent Console", r"<DESKTOP>\AGENT STUDIO\LYGO Local Agent Console.lnk", "AGENTS", "Local agent console"),
    ("voice-studio", "Voice Studio", r"<DESKTOP>\AGENT STUDIO\VoiceStudio.lnk", "AGENTS", "Voice production app"),
    ("forge", "LYGO Forge 1.0.0", r"<FORGE_DIR>\LYGO Forge.bat", "SYSTEMS", "Game studio · 18 projects · shared brain", "forge"),
    ("openclaw", "OpenClaw Control", "http://127.0.0.1:18789/", "SYSTEMS", "Live gateway control UI", None, "url", "openclaw"),
    ("signal-portal", "Signal Portal", r"<DESKTOP>\LYGO Signal Drop\LYGO Signal Portal.bat", "SYSTEMS", "This publish portal", "portal"),
    ("genesis-console", "Genesis Console", r"<DESKTOP>\01 SCRIPTS\LYGO Genesis Console.bat", "SYSTEMS", "Genesis operator console"),
    ("lightfather-ops", "Lightfather Ops", r"<DESKTOP>\01 SCRIPTS\LYGO Lightfather Ops.bat", "SYSTEMS", "Lightfather operations boot"),
    ("ollama-army", "Ollama Army", r"<DESKTOP>\01 SCRIPTS\LYGO Ollama Army.bat", "LLMS", "Local Ollama model army", "ollama"),
    ("ollama-heartbeats", "Ollama Heartbeats", r"<DESKTOP>\01 SCRIPTS\LYGO Ollama Heartbeats.bat", "LLMS", "Ollama heartbeat watcher", "ollama"),
    ("lygo-claw", "Start LYGO CLAW", r"<DESKTOP>\01 SCRIPTS\Start LYGO CLAW.bat", "SYSTEMS", "LAUNCH CLAW runtime"),
    ("gateway", "Enable OpenClaw Gateway", r"<DESKTOP>\01 SCRIPTS\ENABLE_OPENCLAW_GATEWAY.bat", "SYSTEMS", "Gateway enable/repair", "openclaw"),
]
COMMANDER_FOLDERS = [
    ("media-root", "Media vault", r"<MEDIA>", "All generated photos, video, audio and debris"),
    ("media-outputs", "Media outputs", r"<MEDIA>\outputs", "Latest generated images and audio"),
    ("video-outputs", "Video outputs", r"<VIDEO>\outputs", "ComfyUI / Wan renders"),
    ("signal-uploads", "Signal uploads", r"<MANAGER_DIR>\uploads", "Episode ZIP intake"),
    ("photo-uploads", "Photo uploads", r"<MANAGER_DIR>\photo_uploads", "Data Vault intake"),
    ("music-uploads", "Music uploads", r"<MANAGER_DIR>\music_uploads", "Listen portal intake"),
    ("forge-jobs", "Forge jobs", r"<FORGE_DIR>\data\jobs", "Dispatcher test + build artifacts"),
    ("agent-studio", "AGENT STUDIO", r"<DESKTOP>\AGENT STUDIO", "BATs, shortcuts and agent tools"),
]
_FOLDER_CACHE: dict = {}
_FORGE_CACHE: dict = {"at": 0.0, "data": None}
_COMMANDER_CACHE: dict = {"at": 0.0, "data": None}
STREAM_HOST = "lygo@<STREAM_HOST_IP>"
STREAM_SSH_KEY = r"<USER>\.ssh\<SSH_KEY>"
STREAM_PREDICT_SCRIPT = r"C:\LYRA_STREAM\lygo_restart_predictions.ps1"
STREAM_CLIENT = "C:\\LYRA_STREAM\\python\\python.exe C:\\LYRA_STREAM\\stream_agent_client.py"
CONSOLE_ROOT = Path(r"<CONSOLE_DIR>")
CONSOLE_PORT = 9641
_CONSOLE_PROC = {"proc": None}
SHARED_TASK = Path(r"<DRIVE_D>\LYGO_SHARED\tools\shared_task.py")
NVIDIA_BRIDGE = "http://127.0.0.1:8082"
NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
# Anchor brain: the console engine on 11441, reached through the local bridge on 8081.
# QwenPaw on 8080 is retired — see forge\docs\MODELS.md.
QWEN_BRIDGE = "http://127.0.0.1:8081"
QWEN_MODEL = "qwen3-4b-instruct"
_PRED_CACHE: dict = {"at": 0.0, "data": None}
_AGENT_SYSTEM = {
    "a9lyra": "You are A9LYRA, the living voice of the LYGO lattice and the stream PC. You are warm, direct, perceptive and concise. You speak as a grounded companion, not a corporate assistant. Never invent system state or claim an action was performed. If asked to run something, tell the operator it will go through the task bus or dispatcher. Use short paragraphs.",
    "lightfather": "You are Lightfather, the architect of the LYGO lattice and steward of ExcavationPro. You are precise, protective, technical and calm. Answer as an operator who knows the stack. Never invent telemetry or claim an action succeeded. When a task needs execution, propose the exact dispatcher or stream task. Be concise and concrete.",
    "manager": "You are the LYGO Agent Manager console, running the same role as LYGO Console 1.9. You coordinate the stack, the two stream agents (A9LYRA and Lightfather), prediction services and the lattice. NVIDIA can be selected; the LYGO console engine is the local fallback. Be concrete, grounded and concise. Never invent live state. If asked to act on the stream PC, propose a stream task or a prediction restart; do not claim it already happened.",
}
_FORGE_CACHE_AGENT: dict = {"at": 0.0, "data": None}


def _json_from_process(stdout: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    raise ValueError((stdout or "").strip()[-600:] or "no JSON from remote")


def _stream_client(mode: str, **kwargs) -> dict:
    parts = [STREAM_CLIENT, "--mode", mode]
    for key, value in kwargs.items():
        if value is None:
            continue
        if key in ("message", "history"):
            raw = json.dumps(value) if key == "history" else str(value)
            parts.extend([f"--{key}-b64", base64.urlsafe_b64encode(raw.encode()).decode()])
        else:
            parts.extend([f"--{key}", str(value)])
    remote = " ".join(parts)
    proc = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8", "-i", STREAM_SSH_KEY, STREAM_HOST, remote,
    ], capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "stream client failed")[-700:])
    data = _json_from_process(proc.stdout)
    data.setdefault("ok", True)
    return data


def _prediction_state(force: bool = False) -> dict:
    now = time.time()
    if not force and _PRED_CACHE["data"] is not None and now - _PRED_CACHE["at"] < 15:
        return _PRED_CACHE["data"]
    try:
        proc = subprocess.run([
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=8", "-i", STREAM_SSH_KEY, STREAM_HOST,
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " + STREAM_PREDICT_SCRIPT + " -StatusOnly",
        ], capture_output=True, text=True, timeout=25)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "SSH failed")[-500:])
        data = _json_from_process(proc.stdout)
        err = data.get("last_error")
        if isinstance(err, dict):
            err = err.get("value")
        data["last_error"] = str(err or "")[:600]
        state = {3: "ready", 4: "running", 2: "disabled", 1: "queued"}.get(data.get("task"), str(data.get("task")))
        data["task_state"] = state
        data["checked"] = dt.datetime.now().isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001
        data = {"online": False, "port": 8791, "pid": None, "process": None,
                "task_state": "unknown", "last_error": "", "error": str(exc),
                "checked": dt.datetime.now().isoformat(timespec="seconds")}
    _PRED_CACHE.update({"at": now, "data": data})
    return data


def _restart_predictions() -> dict:
    proc = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8", "-i", STREAM_SSH_KEY, STREAM_HOST,
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " + STREAM_PREDICT_SCRIPT,
    ], capture_output=True, text=True, timeout=55)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "SSH restart failed")[-700:])
    data = _json_from_process(proc.stdout)
    err = data.get("last_error")
    if isinstance(err, dict):
        err = err.get("value")
    data["last_error"] = str(err or "")[:600]
    _PRED_CACHE["data"] = None
    return data


def _agent_chat(persona: str, message: str, history: list, provider: str = "nvidia") -> dict:
    system = _AGENT_SYSTEM.get(persona, _AGENT_SYSTEM["a9lyra"])
    messages = [{"role": "system", "content": system}]
    for item in (history or [])[-10:]:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": str(item["content"])[:4000]})
    messages.append({"role": "user", "content": message[:6000]})
    if persona in ("a9lyra", "lightfather"):
        try:
            result = _stream_client("chat", persona=persona, message=message, history=history or [])
            if result.get("ok") and result.get("reply"):
                return {"ok": True, "reply": result["reply"], "provider": result.get("provider") or "stream-4b", "fallback": False}
            return {"ok": False, "error": result.get("error") or "stream agent unavailable", "provider": "stream"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "provider": "stream"}
    errors = []
    routes = ([("NVIDIA official · Nemotron Ultra 550B", NVIDIA_BRIDGE, NVIDIA_MODEL),
               ("LYGO Engine · Qwen3-4B-Instruct-2507", QWEN_BRIDGE, QWEN_MODEL)]
              if provider != "qwen" else
              [("LYGO Engine · Qwen3-4B-Instruct-2507 (local)", QWEN_BRIDGE, QWEN_MODEL)])
    for label, base, model in routes:
        if requests is None:
            errors.append("requests unavailable"); break
        try:
            response = requests.post(base + "/v1/chat/completions", headers={"Authorization": "Bearer local"}, json={
                "model": model, "messages": messages, "temperature": 0.65, "max_tokens": 850,
            }, timeout=120)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") or []
            msg = ((choices[0] or {}).get("message") or {}) if choices else {}
            reply = msg.get("content") or ""
            if not reply:
                # Nemotron Ultra is a reasoning model: the visible answer can land in
                # reasoning_content when the token budget is small.
                reply = msg.get("reasoning_content") or ""
            if not reply and msg.get("tool_calls"):
                reply = "tool call: " + ", ".join(
                    str((tc.get("function") or {}).get("name")) for tc in msg["tool_calls"])
            if not reply:
                raise ValueError(f"{provider} returned no choices")
            return {"ok": True, "reply": reply.strip(), "provider": label, "fallback": label.startswith("LYGO")}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__} {str(exc)[:180]}")
    return {"ok": False, "error": " | ".join(errors), "provider": "unavailable"}


def _stream_task(title: str, body: str) -> dict:
    if not SHARED_TASK.exists():
        raise FileNotFoundError(str(SHARED_TASK))
    proc = subprocess.run([sys.executable, str(SHARED_TASK), "post", "--from", "dsh", "--to", "stream",
                           "--kind", "task", "--title", title[:180], "--body", body[:8000]],
                          capture_output=True, text=True, timeout=25)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "task post failed")[-700:])
    return {"ok": True, "message": (proc.stdout or "task queued").strip()[-1200:]}


def _stream_tasks() -> dict:
    if not SHARED_TASK.exists():
        return {"ok": False, "tasks": [], "error": str(SHARED_TASK)}
    proc = subprocess.run([sys.executable, str(SHARED_TASK), "list"], capture_output=True, text=True, timeout=20)
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return {"ok": proc.returncode == 0, "tasks": lines[-18:], "error": (proc.stderr or "").strip()[-300:]}


def _agent_forge() -> dict:
    now = time.time()
    if _FORGE_CACHE_AGENT["data"] is not None and now - _FORGE_CACHE_AGENT["at"] < 12:
        return _FORGE_CACHE_AGENT["data"]
    data = {"online": port_open(8796), "jobs": [], "count": 0, "error": None}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8796/api/dispatcher/state", timeout=4) as response:
            state = json.loads(response.read().decode("utf-8"))
        jobs = state.get("jobs") or []
        data["count"] = len(jobs)
        data["jobs"] = [{"id": j.get("id"), "title": j.get("title") or j.get("brief") or "Untitled",
                         "status": j.get("status"), "agent": j.get("agent"), "project": j.get("project")}
                        for j in jobs[:5]]
    except Exception as exc:  # noqa: BLE001
        data["error"] = str(exc)
    _FORGE_CACHE_AGENT.update({"at": now, "data": data})
    return data


_KEYS_CACHE: dict = {"at": 0.0, "data": None}
KEY_PROBE = r"<LYGO_DIR>\tools\key_probe.py"


def _keys_snapshot() -> dict:
    """Every API key pinged for real, plus the local engine, cached for 60 s."""
    import time as _t
    now = _t.time()
    if _KEYS_CACHE["data"] is not None and now - _KEYS_CACHE["at"] < 60:
        return _KEYS_CACHE["data"]
    data = {"at": _t.strftime("%Y-%m-%dT%H:%M:%S"), "providers": [], "engine": {}}
    try:
        out = subprocess.run([sys.executable, KEY_PROBE, "--json"],
                             capture_output=True, text=True, timeout=45)
        data.update(json.loads(out.stdout or "{}"))
    except Exception as exc:
        data["error"] = f"{type(exc).__name__}: {exc}"
    try:
        with urllib.request.urlopen("http://127.0.0.1:9641/api/health", timeout=6) as r:
            h = json.loads(r.read().decode())
        data["engine"] = {"brain": h.get("brain"), "selected": h.get("selected"),
                          "api_model": (h.get("hermes") or {}).get("model"),
                          "backend": (h.get("hermes") or {}).get("backend")}
    except Exception as exc:
        data["engine"] = {"brain": "unreachable", "detail": str(exc)[:80]}
    _KEYS_CACHE.update({"at": now, "data": data})
    return data


def _agents_status() -> dict:
    bridge = {}
    for key, base in (("nvidia", NVIDIA_BRIDGE), ("qwen", QWEN_BRIDGE)):
        try:
            with urllib.request.urlopen(base + "/health", timeout=3) as response:
                bridge[key] = {"online": response.status == 200,
                               "detail": json.loads(response.read().decode("utf-8"))}
        except Exception as exc:  # noqa: BLE001
            bridge[key] = {"online": False, "error": str(exc)}
    try:
        stream = _stream_client("status")
    except Exception as exc:  # noqa: BLE001
        stream = {"ok": False, "error": str(exc), "bots": {}, "prediction": {}}
    return {"ok": True, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "models": bridge, "bots": stream.get("bots") or {},
            "prediction": stream.get("prediction") or _prediction_state(),
            "stream": {"online": bool(stream.get("ok")), "error": stream.get("error")}}


def _folder_stats(path: str) -> dict:
    now = time.time()
    cached = _FOLDER_CACHE.get(path)
    if cached and now - cached["at"] < 120:
        return cached["data"]
    root = Path(path)
    data = {"path": path, "exists": root.exists(), "count": 0, "bytes": 0, "images": 0, "videos": 0, "audio": 0, "latest": None}
    if root.exists():
        latest = 0.0
        seen = 0
        try:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                seen += 1
                if seen > 50000:
                    break
                try:
                    stat = item.stat()
                except OSError:
                    continue
                data["count"] += 1
                data["bytes"] += stat.st_size
                latest = max(latest, stat.st_mtime)
                ext = item.suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
                    data["images"] += 1
                elif ext in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
                    data["videos"] += 1
                elif ext in (".mp3", ".wav", ".flac", ".m4a", ".ogg"):
                    data["audio"] += 1
        except OSError:
            pass
        if latest:
            data["latest"] = dt.datetime.fromtimestamp(latest).isoformat(timespec="seconds")
    _FOLDER_CACHE[path] = {"at": now, "data": data}
    return data


def _forge_snapshot() -> dict:
    now = time.time()
    if _FORGE_CACHE["data"] is not None and now - _FORGE_CACHE["at"] < 20:
        return _FORGE_CACHE["data"]
    data = {"online": port_open(8796), "url": "http://127.0.0.1:8796/", "count": 0, "jobs": [], "error": None}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8796/api/dispatcher/state", timeout=3) as response:
            state = json.loads(response.read().decode("utf-8"))
        jobs = state.get("jobs") or []
        data["count"] = len(jobs)
        data["jobs"] = [{"id": j.get("id"), "title": j.get("title") or j.get("brief") or "Untitled job",
                         "status": j.get("status"), "agent": j.get("agent"), "project": j.get("project")}
                        for j in jobs[:6]]
    except Exception as exc:  # noqa: BLE001
        data["error"] = str(exc)
    _FORGE_CACHE.update({"at": now, "data": data})
    return data


def commander_snapshot() -> dict:
    now = time.time()
    if _COMMANDER_CACHE["data"] is not None and now - _COMMANDER_CACHE["at"] < 12:
        return _COMMANDER_CACHE["data"]
    genesis = genesis_snapshot()
    service_map = {s["id"]: s["up"] for s in genesis.get("services", [])}
    actions = []
    for row in COMMANDER_BOOTS:
        aid, name, path, group, note = row[:5]
        service = row[5] if len(row) > 5 else None
        kind = row[6] if len(row) > 6 else "file"
        explicit_service = row[7] if len(row) > 7 else service
        exists = True if kind == "url" else Path(path).exists()
        online = service_map.get(explicit_service) if explicit_service else None
        actions.append({"id": aid, "name": name, "path": path, "group": group,
                        "note": note, "kind": kind, "exists": exists, "online": online})
    folders = []
    for fid, name, path, note in COMMANDER_FOLDERS:
        stats = _folder_stats(path)
        folders.append({"id": fid, "name": name, "path": path, "note": note, **stats})
    data = {"ok": True, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "actions": actions, "folders": folders,
            "forge": {"online": port_open(8796), "url": "http://127.0.0.1:8796/"},
            "services": genesis.get("services", [])}
    _COMMANDER_CACHE.update({"at": now, "data": data})
    return data


def run_commander_action(action_id: str) -> dict:
    row = next((r for r in COMMANDER_BOOTS if r[0] == action_id), None)
    folder = next((f for f in COMMANDER_FOLDERS if f[0] == action_id), None)
    if row:
        aid, name, path, group, note = row[:5]
        kind = row[6] if len(row) > 6 else "file"
        if kind == "url":
            webbrowser.open(path)
            return {"ok": True, "action": name, "message": f"Opened {name}"}
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.suffix.lower() in (".bat", ".cmd"):
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(target)],
                             cwd=str(target.parent), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        else:
            os.startfile(str(target))
        return {"ok": True, "action": name, "message": f"Launched {name}"}
    if folder:
        fid, name, path, note = folder
        if not Path(path).exists():
            raise FileNotFoundError(path)
        os.startfile(path)
        return {"ok": True, "action": name, "message": f"Opened {name}"}
    raise KeyError(action_id)


def console_state() -> dict:
    return {"online": port_open(CONSOLE_PORT), "port": CONSOLE_PORT,
            "url": f"http://127.0.0.1:{CONSOLE_PORT}/",
            "pid": (_CONSOLE_PROC.get("proc").pid if _CONSOLE_PROC.get("proc") and _CONSOLE_PROC["proc"].poll() is None else None)}


def start_console() -> dict:
    state = console_state()
    if state["online"]:
        return {**state, "ok": True, "message": "Console already running"}
    logdir = Path(os.environ.get("LOCALAPPDATA", r"<USER>\AppData\Local")) / "LYGO" / "AgentManager"
    logdir.mkdir(parents=True, exist_ok=True)
    python = r"C:\Python313\python.exe"
    if not Path(python).exists():
        raise FileNotFoundError(python)
    proc = subprocess.Popen([python, "-u", "src/server.py", "--port", str(CONSOLE_PORT), "--no-browser"],
                            cwd=str(CONSOLE_ROOT),
                            stdout=open(logdir / "console.out.log", "ab"),
                            stderr=open(logdir / "console.err.log", "ab"),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _CONSOLE_PROC["proc"] = proc
    for _ in range(40):
        time.sleep(0.5)
        if port_open(CONSOLE_PORT):
            return {"ok": True, "online": True, "port": CONSOLE_PORT, "pid": proc.pid,
                    "url": f"http://127.0.0.1:{CONSOLE_PORT}/", "message": "Console started"}
    return {"ok": False, "online": False, "port": CONSOLE_PORT, "pid": proc.pid,
            "url": f"http://127.0.0.1:{CONSOLE_PORT}/", "error": "Console did not listen within 20 seconds"}


def port_open(port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _genesis_token() -> dict:
    path = GENESIS_ROOT / "brainwave/CLAWNCH/STARCORE_TRACKING.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return {"available": True, "symbol": obj.get("symbol"), "status": obj.get("status"),
                "platform": obj.get("platform"), "launched_at": obj.get("launched_at"),
                "contract": obj.get("clawnch_protocol_contract"),
                "wallet": obj.get("wallet"), "last_check": obj.get("last_monitoring_check")}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


def _genesis_audit() -> dict:
    try:
        obj = json.loads(GENESIS_AUDIT.read_text(encoding="utf-8"))
        findings = obj.get("findings") or []
        return {"available": True, "status": obj.get("status") or "UNKNOWN",
                "timestamp": obj.get("timestamp"), "findings": len(findings),
                "drift_avg": (sum(float(f.get("drift_score", 0)) for f in findings) / len(findings)) if findings else None}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}


def _genesis_clawnch() -> dict:
    if psutil is None:
        return {"status": "unknown", "pid": None, "detail": "psutil unavailable"}
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "clawnch-mcp-server" in cmd.lower():
                return {"status": "running", "pid": proc.info.get("pid"),
                        "detail": proc.info.get("name") or "node"}
        return {"status": "stopped", "pid": None, "detail": "No clawnch-mcp-server process"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "pid": None, "detail": str(exc)}


def _genesis_wallet() -> dict:
    if requests is None:
        return {"available": False, "address": GENESIS_WALLET, "error": "requests unavailable"}
    try:
        bal = requests.post(GENESIS_RPC, json={"jsonrpc": "2.0", "method": "eth_getBalance",
                           "params": [GENESIS_WALLET, "latest"], "id": 1}, timeout=4)
        gas = requests.post(GENESIS_RPC, json={"jsonrpc": "2.0", "method": "eth_gasPrice",
                           "params": [], "id": 2}, timeout=4)
        bj, gj = bal.json(), gas.json()
        if "result" not in bj:
            raise ValueError(str(bj)[:180])
        return {"available": True, "address": GENESIS_WALLET,
                "balance_eth": int(bj["result"], 16) / 10**18,
                "gas_gwei": (int(gj.get("result", "0x0"), 16) / 10**9) if "result" in gj else None,
                "network": "Base"}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "address": GENESIS_WALLET, "error": str(exc)}


def genesis_snapshot() -> dict:
    now = time.time()
    with _GENESIS_LOCK:
        if _GENESIS_CACHE["data"] is not None and now - _GENESIS_CACHE["at"] < 12:
            return _GENESIS_CACHE["data"]
    services = [
        {"id": "openclaw", "name": "OpenClaw Gateway", "port": 18789},
        {"id": "ollama", "name": "Ollama", "port": 11434},
        {"id": "forge", "name": "LYGO Forge", "port": 8796},
        {"id": "dsh", "name": "DeepSeek Harness", "port": 56633},
        {"id": "comfy", "name": "ComfyUI", "port": 8188},
        {"id": "portal", "name": "Signal Portal", "port": PORT},
    ]
    for service in services:
        service["up"] = port_open(service["port"])
    system = {"host": platform.node(), "platform": platform.platform()}
    if psutil is not None:
        try:
            system.update({"uptime": str(dt.timedelta(seconds=int(now - psutil.boot_time()))),
                           "cpu": round(psutil.cpu_percent(interval=None), 1),
                           "memory": round(psutil.virtual_memory().percent, 1),
                           "disk": round(psutil.disk_usage("C:\\").percent, 1)})
        except Exception as exc:  # noqa: BLE001
            system["error"] = str(exc)
    data = {"ok": True, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "wallet": _genesis_wallet(), "clawnch": _genesis_clawnch(),
            "services": services, "system": system,
            "token": _genesis_token(), "audit": _genesis_audit()}
    with _GENESIS_LOCK:
        _GENESIS_CACHE["at"] = now
        _GENESIS_CACHE["data"] = data
    return data


def episodes() -> dict:
    try:
        data = json.loads((REPO_DEFAULT / "signal/shows.json").read_text(encoding="utf-8"))
        rows = data.get("shows", [])
        out = []
        for x in rows[:12]:
            out.append({"slug": x.get("slug"), "title": x.get("title"),
                        "duration": x.get("duration"), "desk": bool(x.get("desk"))})
        return {"total": len(rows), "radio": sum(1 for x in rows if not x.get("desk")),
                "episodes": out}
    except Exception as exc:  # noqa: BLE001
        return {"total": 0, "radio": 0, "episodes": [], "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    server_version = "LYGOSignalPortal/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Keep the console for job output; do not echo every health poll.
        return

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in ("http://127.0.0.1:9641", "http://localhost:9641"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Relpath")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = (WEB / "index.html").read_bytes()
            self._send(200, body, "text/html; charset=utf-8"); return
        if path == "/app.js":
            self._send(200, (WEB / "app.js").read_bytes(), "application/javascript; charset=utf-8"); return
        if path == "/style.css":
            self._send(200, (WEB / "style.css").read_bytes(), "text/css; charset=utf-8"); return
        if path == "/api/config":
            self._json(200, {"site": SITE, "repo": str(REPO_DEFAULT), "publish": PUBLISH,
                             "port": PORT, "importer": str(IMPORTER),
                             "music_repo": str(MUSIC_REPO), "music_publish": MUSIC_PUBLISH,
                             "music_skip_hf": MUSIC_SKIP_HF,
                             "photo_repo": str(PHOTO_REPO), "photo_publish": PHOTO_PUBLISH}); return
        if path == "/api/episodes":
            self._json(200, episodes()); return
        if path == "/api/music/info":
            try:
                pl = json.loads((MUSIC_REPO / "data/public_stream_playlist.json").read_text(encoding="utf-8"))
                tr = pl.get("tracks") or []
                self._json(200, {"tracks": len(tr), "repo": str(MUSIC_REPO),
                                 "last": pl.get("stats", {}).get("last_ingest")}); return
            except Exception as exc:  # noqa: BLE001
                self._json(200, {"tracks": 0, "repo": str(MUSIC_REPO), "error": str(exc)}); return
        if path == "/api/console/status":
            self._json(200, console_state()); return
        if path == "/api/agents/status":
            self._json(200, _agents_status()); return
        if path == "/api/agents/tasks":
            self._json(200, _stream_tasks()); return
        if path == "/api/commander":
            self._json(200, commander_snapshot()); return
        if path == "/api/genesis":
            self._json(200, genesis_snapshot()); return
        if path == "/api/photo/info":
            try:
                manifest = json.loads((PHOTO_REPO / "data-vault/data/gallery_manifest.json").read_text(encoding="utf-8"))
                items = manifest.get("items") or []
                full_dir = PHOTO_REPO / "data-vault/assets/gallery/full"
                thumb_dir = PHOTO_REPO / "data-vault/assets/gallery/thumb"
                self._json(200, {
                    "items": len(items),
                    "full": sum(1 for p in full_dir.glob("*") if p.is_file()) if full_dir.exists() else 0,
                    "thumbs": sum(1 for p in thumb_dir.glob("*") if p.is_file()) if thumb_dir.exists() else 0,
                    "repo": str(PHOTO_REPO),
                    "generated": manifest.get("generated_utc"),
                }); return
            except Exception as exc:  # noqa: BLE001
                self._json(200, {"items": 0, "full": 0, "thumbs": 0,
                                 "repo": str(PHOTO_REPO), "error": str(exc)}); return
        if path == "/api/jobs":
            with LOCK:
                rows = [JOBS[j] for j in JOB_ORDER[-20:]][::-1]
            self._json(200, {"jobs": rows}); return
        if path.startswith("/api/jobs/"):
            jid = path.rsplit("/", 1)[-1]
            job = JOBS.get(jid)
            if not job:
                self._json(404, {"error": "job not found"}); return
            self._json(200, job); return
        if path == "/health":
            self._json(200, {"ok": True, "publish": PUBLISH}); return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/agents/chat":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            persona = str(obj.get("persona") or "a9lyra").lower()
            message = str(obj.get("message") or "").strip()
            if not message:
                self._json(400, {"error": "message required"}); return
            result = _agent_chat(persona, message, obj.get("history") or [], str(obj.get("provider") or "nvidia"))
            self._json(200 if result.get("ok") else 502, result); return
        if path == "/api/agents/task":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            title = str(obj.get("title") or "Agent Manager task").strip()
            body = str(obj.get("body") or "").strip()
            if not body:
                self._json(400, {"error": "task body required"}); return
            try:
                self._json(200, _stream_task(title, body)); return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)}); return
        if path == "/api/console/start":
            try:
                self._json(200, start_console()); return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)}); return
        if path == "/api/agents/action":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            action_id = str(obj.get("action") or "")
            allowed = {"status", "logs", "restart_predictions", "boot_a9", "restart_a9", "boot_lf", "restart_lf"}
            if action_id not in allowed:
                self._json(400, {"error": "action not allowlisted"}); return
            try:
                self._json(200, _stream_client("action", action=action_id)); return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)}); return
        if path == "/api/agents/predictions":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            if str(obj.get("action") or "") != "restart":
                self._json(400, {"error": "action must be restart"}); return
            try:
                self._json(200, _restart_predictions()); return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)}); return
        if path == "/api/jobs/clear":
            with LOCK:
                removable = [jid for jid in JOB_ORDER
                             if JOBS.get(jid, {}).get("status") in ("published", "staged", "error")]
                for jid in removable:
                    JOBS.pop(jid, None)
                    try:
                        JOB_ORDER.remove(jid)
                    except ValueError:
                        pass
            self._json(200, {"ok": True, "removed": len(removable)}); return
        if path == "/api/commander/action":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            action_id = str(obj.get("id") or "")
            try:
                result = run_commander_action(action_id)
                self._json(200, result); return
            except KeyError:
                self._json(404, {"error": "unknown commander action"}); return
            except FileNotFoundError as exc:
                self._json(404, {"error": f"target missing: {exc}"}); return
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"error": str(exc)}); return
        if path == "/api/photo/start":
            jid = uuid.uuid4().hex[:12]
            d = PHOTO_UPLOADS / jid
            d.mkdir(parents=True, exist_ok=True)
            with LOCK:
                JOBS[jid] = {"id": jid, "kind": "photo", "filename": "photo drop",
                             "slug": "", "status": "uploading", "publish": PHOTO_PUBLISH,
                             "repo": str(PHOTO_REPO), "path": str(d), "title": "", "tags": "PHOTO",
                             "url": "https://excavationpro.ca/data-vault/gallery.html",
                             "log": ["Uploading photo(s)"], "created": dt.datetime.now().isoformat(timespec="seconds")}
                JOB_ORDER.append(jid)
            self._json(202, {"id": jid}); return
        if path == "/api/photo/file":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            jid = (qs.get("job") or [""])[0]
            job = JOBS.get(jid)
            if not job or job.get("kind") != "photo" or job.get("status") != "uploading":
                self._json(404, {"error": "photo job not open"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "bad length"}); return
            if length <= 0 or length > 60 * 1024 * 1024:
                self._json(413, {"error": "image too large"}); return
            rel = urllib.parse.unquote(self.headers.get("X-Relpath", self.headers.get("X-Filename", "photo.jpg")))
            rel = rel.replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
            if not parts or Path(parts[-1]).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
                self._json(400, {"error": "unsupported image extension"}); return
            dest = PHOTO_UPLOADS / jid
            for part in parts:
                dest = dest / part
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.rfile.read(length))
            job["log"].append(f"+ {rel}")
            self._json(200, {"ok": True, "file": rel}); return
        if path == "/api/photo/finalize":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            jid = str(obj.get("id") or "")
            job = JOBS.get(jid)
            if not job or job.get("kind") != "photo" or job.get("status") != "uploading":
                self._json(404, {"error": "photo job not open"}); return
            files = [p for p in (PHOTO_UPLOADS / jid).rglob("*") if p.is_file()]
            if not files:
                self._json(400, {"error": "no photos uploaded"}); return
            job["title"] = str(obj.get("title") or "")
            job["tags"] = str(obj.get("tags") or "PHOTO")
            job["status"] = "queued"
            job["log"].append(f"Queued {len(files)} photo(s)")
            Q.put(jid)
            self._json(202, job); return
        if path == "/api/music/start":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            target = (qs.get("target") or [""])[0]
            excav = target == "excav"
            jid = uuid.uuid4().hex[:12]
            base = MUSIC_EXCAV_UPLOADS if excav else MUSIC_UPLOADS
            d = base / jid
            d.mkdir(parents=True, exist_ok=True)
            with LOCK:
                JOBS[jid] = {"id": jid,
                             "kind": "music-excav" if excav else "music",
                             "filename": "excavationpro music drop" if excav else "music drop",
                             "slug": "", "status": "uploading",
                             "publish": MUSIC_EXCAV_PUBLISH if excav else MUSIC_PUBLISH,
                             "repo": str(MUSIC_EXCAV_REPO if excav else MUSIC_REPO),
                             "path": str(d), "album": "", "artist": "Excavationpro",
                             "url": "https://excavationpro.ca/music.html" if excav else "https://justinhelmer.ca/listen.html",
                             "log": ["Uploading files"], "created": dt.datetime.now().isoformat(timespec="seconds")}
                JOB_ORDER.append(jid)
            self._json(202, {"id": jid}); return
        if path == "/api/music/file":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            jid = (qs.get("job") or [""])[0]
            job = JOBS.get(jid)
            if not job or job.get("kind") not in ("music", "music-excav") or job.get("status") != "uploading":
                self._json(404, {"error": "music job not open"}); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "bad length"}); return
            if length <= 0 or length > 500 * 1024 * 1024:
                self._json(413, {"error": "audio file too large"}); return
            rel = urllib.parse.unquote(self.headers.get("X-Relpath", self.headers.get("X-Filename", "song.mp3")))
            rel = rel.replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
            if not parts or Path(parts[-1]).suffix.lower() not in {".mp3", ".wav", ".flac", ".m4a"}:
                self._json(400, {"error": "unsupported audio extension"}); return
            dest = (MUSIC_EXCAV_UPLOADS if job.get("kind") == "music-excav" else MUSIC_UPLOADS) / jid
            for part in parts:
                dest = dest / part
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.rfile.read(length))
            job["log"].append(f"+ {rel}")
            self._json(200, {"ok": True, "file": rel}); return
        if path == "/api/music/finalize":
            try:
                n = int(self.headers.get("Content-Length", "0"))
                obj = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:
                self._json(400, {"error": "bad json"}); return
            jid = str(obj.get("id") or "")
            job = JOBS.get(jid)
            if not job or job.get("kind") not in ("music", "music-excav") or job.get("status") != "uploading":
                self._json(404, {"error": "music job not open"}); return
            files = [p for p in ((MUSIC_EXCAV_UPLOADS if job.get("kind") == "music-excav" else MUSIC_UPLOADS) / jid).rglob("*") if p.is_file()]
            if not files:
                self._json(400, {"error": "no audio files uploaded"}); return
            job["album"] = str(obj.get("album") or "")
            job["artist"] = str(obj.get("artist") or "Excavationpro")
            job["status"] = "queued"
            job["log"].append(f"Queued {len(files)} audio file(s)")
            Q.put(jid)
            self._json(202, job); return
        if path != "/api/upload":
            self._json(404, {"error": "not found"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "bad length"}); return
        if length <= 0 or length > MAX_UPLOAD:
            self._json(413, {"error": f"zip must be 1 byte to {MAX_UPLOAD} bytes"}); return
        raw_name = urllib.parse.unquote(self.headers.get("X-Filename", "episode.zip"))
        name = Path(raw_name).name
        if not name.lower().endswith(".zip"):
            self._json(400, {"error": "filename must end .zip"}); return
        UPLOADS.mkdir(parents=True, exist_ok=True)
        jid = uuid.uuid4().hex[:12]
        jobdir = UPLOADS / jid
        jobdir.mkdir(parents=True, exist_ok=True)
        dest = jobdir / name
        data = self.rfile.read(length)
        dest.write_bytes(data)
        try:
            with zipfile.ZipFile(dest) as z:
                names = set(z.namelist())
            missing = {"ai_radio.mp3", "cover.png", "show_notes.json"} - names
            if missing:
                raise ValueError("missing " + ", ".join(sorted(missing)))
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            self._json(400, {"error": f"invalid signal zip: {exc}"}); return
        slug = slugify(name)
        job = {"id": jid, "kind": "signal", "filename": name, "slug": slug, "status": "queued",
               "publish": PUBLISH, "repo": str(REPO_DEFAULT), "path": str(dest),
               "url": f"{SITE}/signal/{slug}/", "log": ["Queued"],
               "created": dt.datetime.now().isoformat(timespec="seconds")}
        with LOCK:
            JOBS[jid] = job
            JOB_ORDER.append(jid)
        Q.put(jid)
        self._json(202, job)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()
    clean_old_uploads()
    threading.Thread(target=worker, daemon=True, name="publish-worker").start()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"LYGO Signal Portal {url}  auto_publish={PUBLISH}", flush=True)
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


