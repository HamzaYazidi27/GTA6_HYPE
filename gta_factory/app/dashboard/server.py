"""Local dashboard: start jobs, follow progress, read the fact-check, edit the script, review the package.

Standard library only (``http.server``). It binds to 127.0.0.1 by default and
protects every state-changing request:

* the ``Host`` header must be a loopback name (blocks DNS-rebinding pages),
* mutating requests need the ``X-Factory: 1`` header, which a foreign web page
  cannot send without a CORS preflight this server never answers.

Jobs run in a separate ``python -m gta_factory resume`` process, so a long
render never blocks the dashboard and survives a browser refresh.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..core.errors import FactoryError
from ..core.utils import now_iso, read_json, sha1, write_json
from ..database.db import get_db

HERE = Path(__file__).parent
JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,80}$")
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}
UPLOAD_MAX = 8 * 1024**3  # one trailer can be big
_FOOTAGE_TASKS: dict[int, subprocess.Popen] = {}


def spawn_runner(settings: Any, job_id: str, *, from_step: str | None = None, force: bool = False) -> None:
    cmd = [sys.executable, "-m", "gta_factory", "resume", job_id]
    if from_step:
        cmd += ["--from", from_step]
    if force:
        cmd.append("--force")
    out = open(settings.jobs_dir / job_id / "pipeline.out", "ab")
    subprocess.Popen(cmd, cwd=str(settings.root), stdout=out, stderr=out, start_new_session=True)


# --------------------------------------------------------------------------- footage library
def _pid_alive(pid: int) -> bool:
    proc = _FOOTAGE_TASKS.get(pid)
    if proc is not None:
        return proc.poll() is None
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def footage_task(settings: Any) -> dict[str, Any]:
    from ...modules.footage.library import footage_dir

    root = footage_dir(settings)
    info = read_json(root / ".task.json", {}) or {}
    log = root / ".task.log"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-60:] if log.exists() else []
    return {"running": bool(info.get("pid")) and _pid_alive(int(info["pid"])), "args": info.get("args") or [], "started": info.get("started"), "log": lines}


def spawn_footage_task(settings: Any, args: list[str]) -> None:
    """Run ``python -m gta_factory footage <args>`` in the background (one at a time), logging to .task.log."""
    from ...modules.footage.library import footage_dir

    if footage_task(settings)["running"]:
        raise FactoryError("A footage task is already running.", hint="Wait for it to finish (its log is shown on the Footage page).")
    root = footage_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".task.log", "wb") as out:
        proc = subprocess.Popen([sys.executable, "-m", "gta_factory", "footage", *args], cwd=str(settings.root), stdout=out, stderr=out, start_new_session=True)
    _FOOTAGE_TASKS[proc.pid] = proc
    write_json(root / ".task.json", {"pid": proc.pid, "args": args, "started": now_iso()})


def footage_payload(settings: Any) -> dict[str, Any]:
    from collections import Counter
    from urllib.parse import quote

    from ...modules.footage import library as lib
    from ...modules.footage.select import ratio_band

    index = lib.build_index(settings)
    items = lib.load_items(settings, index)
    videos = []
    for v in index["videos"]:
        its = [it for it in items["shots"] if it["file"] == v["file"]]
        seen = Counter(tag for it in its for tag in it["tags"] if tag not in v["tags"])
        videos.append({
            "file": v["file"],
            "duration": round(v.get("duration", 0), 1),
            "shots": len(its),
            "tagged": sum(1 for it in its if set(it["tags"]) - set(v["tags"])),  # shots with tags of their own
            "tags": v["tags"],
            "seen": [f"{tag} ({n})" for tag, n in seen.most_common(14)],
            "segments": [s for s in (v.get("sidecar") or {}).get("segments") or [] if isinstance(s, dict)],
            "poster": "/api/footage/poster?file=" + quote(v["file"]),
        })
    photos = [{"file": p["file"], "tags": p["tags"], "auto": sorted(tag for tag in it["tags"] if tag not in p["tags"]), "url": "/footage-files/" + quote(p["file"])} for p, it in zip(index["photos"], items["photos"])]
    ratio, lo, hi = ratio_band(settings)
    vision = None
    if settings.get("footage.auto_tag", True):
        from ...modules.footage.autotag import pending, vision_status

        ready, detail = vision_status(settings)
        vision = {"ready": ready, "detail": detail, "pending": len(pending(settings, index)) if (videos or photos) else 0}
    from ...modules.footage.games import MAIN, game_settings, games

    others = []
    for gid, g in games(settings).items():
        if gid == MAIN:
            continue
        summ = lib.library_summary(lib.build_index(game_settings(settings, gid)))
        others.append({"id": gid, "name": g["name"], "short": g["short"], "dir": str(lib.footage_dir(game_settings(settings, gid))), "summary": {k: v for k, v in summ.items() if k != "skipped"}})
    return {
        "games": others,
        "dir": str(lib.footage_dir(settings)),
        "summary": lib.library_summary(index),
        "videos": videos,
        "photos": photos,
        "concepts": lib.concept_coverage(settings, items),
        "ratio": {"target": ratio, "min": lo, "max": hi},
        "vision": vision,
        "task": footage_task(settings),
    }


def job_payload(settings: Any, job_id: str) -> dict[str, Any] | None:
    db = get_db(settings.db_path)
    job = db.get_job(job_id)
    if job is None:
        return None
    root = settings.jobs_dir / job_id
    out_dir = settings.outputs_dir / job_id
    story = read_json(root / "story" / "story.json")
    outline = read_json(root / "story" / "outline.json")
    metadata = read_json(root / "metadata" / "metadata.json")
    video = next(iter(sorted(out_dir.glob("*.mp4"))), None) if out_dir.exists() else None
    thumbs = sorted(p.name for p in (root / "thumbnails").glob("thumb_*.png")) if (root / "thumbnails").exists() else []
    return {
        "job": job,
        "steps": db.get_steps(job_id),
        "story": story and {k: story.get(k) for k in ("novelty_score", "sub_scores", "decision", "decision_reason", "angle", "angles", "similar_stories", "open_questions", "working_title")},
        "fact_check": read_json(root / "research" / "fact_check.json"),
        "outline": outline,
        "script": read_json(root / "script" / "script.json"),
        "scenes": read_json(root / "scenes" / "scene_breakdown.json"),
        "qc": read_json(root / "qc" / "qc_report.json"),
        "rights": read_json(root / "assets" / "rights_review.json"),
        "metadata": metadata and {k: metadata.get(k) for k in ("title", "title_options", "description", "tags", "chapters", "pinned_comment")},
        "video": f"/files/{job_id}/out/{video.name}" if video else None,
        "thumbnails": [f"/files/{job_id}/job/thumbnails/{t}" for t in thumbs],
        "review": (out_dir / "REVIEW.md").read_text(encoding="utf-8") if (out_dir / "REVIEW.md").exists() else None,
    }


def voice_options(src: dict[str, Any]) -> dict[str, str]:
    """The voice chosen in the dashboard (provider + voice id), checked, as job options."""
    from ...modules.voice_generation.samples import check_choice

    tts, voice = str(src.get("tts") or "").strip(), str(src.get("voice") or "").strip()
    check_choice(tts or None, voice or None)
    return {**({"tts": tts} if tts else {}), **({"voice": voice} if voice else {})}


def write_script_md(settings: Any, job_id: str, script: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    root = settings.jobs_dir / job_id
    if script.get("kind") == "topic":
        from ...modules.topic.writer import script_markdown

        text = script_markdown(script, facts)
    else:
        from ...modules.script_generation.writer import render_script_markdown

        text = render_script_markdown(script, read_json(root / "story" / "story.json", {}), facts)
    (root / "script" / "script.md").write_text(text, encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "GTA6Factory/1.0"
    settings: Any = None

    # ------------------------------------------------------------------ plumbing
    def log_message(self, fmt: str, *args: Any) -> None:  # quiet console
        pass

    allowed_hosts: frozenset[str] = frozenset(LOOPBACK)

    def _host_ok(self) -> bool:
        raw = self.headers.get("Host") or ""
        host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.rsplit(":", 1)[0]
        return host in self.allowed_hosts

    def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, code: int, message: str, hint: str = "") -> None:
        self._json({"error": message, "hint": hint}, code)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise FactoryError("request too large")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise FactoryError("invalid JSON body") from exc
        return data if isinstance(data, dict) else {}

    def _guard(self, mutating: bool) -> bool:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "Host not allowed")
            return False
        if mutating and self.headers.get("X-Factory") != "1":
            self._error(HTTPStatus.FORBIDDEN, "missing X-Factory header")
            return False
        return True

    # ------------------------------------------------------------------ files
    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "file not found")
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
            end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
            end = min(end, size - 1)
            if start > end:
                self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"", ctype, {"Content-Range": f"bytes */{size}"})
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with path.open("rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with path.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

    def _file_route(self, parts: list[str]) -> None:
        # /files/<job>/<out|job>/<relative path>
        if len(parts) < 4 or not JOB_ID.match(parts[1]) or parts[2] not in ("out", "job"):
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        base = (self.settings.outputs_dir if parts[2] == "out" else self.settings.jobs_dir) / parts[1]
        target = (base / "/".join(unquote(p) for p in parts[3:])).resolve()
        if not str(target).startswith(str(base.resolve()) + "/"):
            self._error(HTTPStatus.FORBIDDEN, "outside the job folder")
            return
        self._serve_file(target)

    def _footage_file(self, rel: str) -> Path:
        from ...modules.footage.library import footage_dir

        base = footage_dir(self.settings).resolve()
        target = (base / unquote(rel)).resolve()
        if not str(target).startswith(str(base) + "/"):
            raise FactoryError("outside the footage library")
        return target

    def _poster(self, rel: str) -> None:
        from ...modules.footage.library import footage_dir

        src = self._footage_file(rel)
        if not src.is_file():
            self._error(HTTPStatus.NOT_FOUND, "file not found")
            return
        out = footage_dir(self.settings) / ".posters" / f"{sha1(rel + str(src.stat().st_size))[:16]}.jpg"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            dur = 0.0
            try:
                dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=30).stdout.strip() or 0)
            except (ValueError, subprocess.SubprocessError, OSError):
                pass
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * 0.3:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)], capture_output=True, timeout=60)
        self._serve_file(out)

    def _upload(self, query: dict[str, list[str]]) -> None:
        """Raw request body = one file. ``?name=<file name>&tags=lucia,beach``."""
        from ...modules.footage import library as lib

        name = re.sub(r"[^\w.\- ()\[\]]+", "_", Path(unquote((query.get("name") or [""])[0])).name).strip(" .")[:150]
        ext = Path(name).suffix.lower()
        if not name or ext not in lib.VIDEO_EXT | lib.PHOTO_EXT:
            raise FactoryError("Only videos (mp4 mov webm mkv m4v) and pictures (jpg png webp) can be added.")
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > UPLOAD_MAX:
            raise FactoryError("Empty or too large file (max 8 GB).")
        from ...modules.footage.games import game_settings

        gs = game_settings(self.settings, (query.get("game") or [""])[0] or None)
        folder = lib.footage_dir(gs) / ("videos" if ext in lib.VIDEO_EXT else "photos")
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / name
        k = 2
        while dest.exists():
            dest = folder / f"{Path(name).stem} ({k}){ext}"
            k += 1
        tmp = folder / f".{dest.name}.part"  # hidden while it uploads: the indexer skips it
        left = size
        with tmp.open("wb") as fh:
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        if left:
            tmp.unlink(missing_ok=True)
            raise FactoryError("The upload was interrupted.")
        tmp.rename(dest)
        tags = [x for x in (query.get("tags") or [""])[0].split(",") if x.strip()]
        if tags:
            lib.set_tags(gs, dest, add=tags)
        self._json({"file": dest.relative_to(lib.footage_dir(gs)).as_posix()}, 201)

    # ------------------------------------------------------------------ routes
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if not self._guard(False):
            return
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        s = self.settings
        try:
            if not parts:
                self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8", {"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})
            elif parts == ["api", "jobs"]:
                db = get_db(s.db_path)
                from ...modules.footage.games import MAIN, games

                self._json({
                    "jobs": db.list_jobs(200),
                    "counts": db.job_counts(),
                    "formats": {k: v.get("label") for k, v in (s.get("formats") or {}).items() if not v.get("footage_only") and not v.get("topic_only")},
                    "games": [{"id": gid, "name": g["name"], "short": g["short"]} for gid, g in games(s).items() if gid != MAIN],
                })
            elif len(parts) == 3 and parts[:2] == ["api", "jobs"] and JOB_ID.match(parts[2]):
                payload = job_payload(s, parts[2])
                self._json(payload) if payload else self._error(404, "unknown job")
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "log" and JOB_ID.match(parts[2]):
                n = int((parse_qs(url.query).get("lines") or ["200"])[0])
                path = s.jobs_dir / parts[2] / "pipeline.log"
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-min(n, 2000) :] if path.exists() else []
                self._json({"lines": lines})
            elif parts[0] == "files":
                self._file_route(parts)
            elif parts == ["api", "voices"]:
                from ...modules.voice_generation.samples import catalog

                self._json(catalog(s))
            elif len(parts) == 2 and parts[0] == "voice-samples" and re.match(r"^[A-Za-z0-9_.\-]+\.wav$", parts[1]):
                self._serve_file(s.jobs_dir / ".cache" / "voice_samples" / parts[1])
            elif parts == ["brand", "logo"]:  # the channel logo, for the dashboard header
                from ...modules.branding import logo_file

                logo = logo_file(s)
                if logo and logo.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                    self._serve_file(logo)
                else:
                    self._error(404, "no channel logo")
            elif parts == ["api", "footage"]:
                self._json(footage_payload(s))
            elif parts == ["api", "footage", "task"]:
                self._json(footage_task(s))
            elif parts == ["api", "footage", "poster"]:
                self._poster((parse_qs(url.query).get("file") or [""])[0])
            elif parts[0] == "footage-files" and len(parts) > 1:
                self._serve_file(self._footage_file("/".join(parts[1:])))
            else:
                self._error(404, "not found")
        except FactoryError as exc:
            self._error(400, exc.message, exc.hint)

    def do_POST(self) -> None:
        if not self._guard(True):
            return
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        s = self.settings
        try:
            if parts == ["api", "footage", "upload"]:
                self._upload(parse_qs(url.query))
                return
            body = self._body()
            if parts == ["api", "voices", "sample"]:  # the sample sentence read by one voice
                from ...modules.voice_generation.samples import sample

                path = sample(s, str(body.get("tts") or "kokoro"), str(body.get("voice") or ""), out_dir=s.jobs_dir / ".cache" / "voice_samples")
                self._json({"url": f"/voice-samples/{path.name}"})
                return
            if parts[:2] == ["api", "footage"] and len(parts) == 3:
                tags = body.get("tags") or ""
                tags = ",".join(tags) if isinstance(tags, list) else str(tags)
                game_arg: list[str] = []
                if body.get("game"):
                    from ...modules.footage.games import MAIN, resolve

                    gid = resolve(s, str(body["game"]))
                    game_arg = [] if gid == MAIN else ["--game", gid]
                if parts[2] == "add":
                    link = str(body.get("url") or "").strip()
                    if not re.match(r"^https?://\S+$", link):
                        raise FactoryError("Give a video or picture URL (http...).")
                    spawn_footage_task(s, ["add", link] + (["--tags", tags] if tags.strip() else []) + game_arg)
                elif parts[2] == "update":
                    spawn_footage_task(s, ["update"] + game_arg)
                elif parts[2] == "autotag":
                    spawn_footage_task(s, ["autotag"] + game_arg)
                elif parts[2] == "process":  # after uploads: stills from new videos, then auto-tags
                    spawn_footage_task(s, ["stills"] + game_arg)
                elif parts[2] == "details":  # a "details you missed" video from this footage
                    from ..core.pipeline import create_details_job

                    video = str(body.get("video") or "").strip()
                    fmt = "details" if body.get("format") == "details" else "short"
                    count = int(body.get("count") or 0) or None
                    job_id = create_details_job(s, videos=[video] if video else [], fmt=fmt, count=count, options=voice_options(body))
                    spawn_runner(s, job_id)
                    self._json({"id": job_id}, 201)
                    return
                elif parts[2] == "find":
                    from ...modules.footage.select import preview

                    self._json({"rows": preview(s, str(body.get("text") or "")[:2000])})
                    return
                else:
                    self._error(404, "not found")
                    return
                self._json({"ok": True, "task": footage_task(s)}, 202)
                return
            if parts == ["api", "topic"]:  # a comparison or a top list
                from ..core.pipeline import create_topic_job

                sources = [u for u in (body.get("sources") or []) if isinstance(u, str) and re.match(r"^https?://", u.strip())]
                options = {k: v for k, v in (body.get("options") or {}).items() if k in ("no_search", "audit", "whisper", "target_seconds", "today")} | voice_options(body.get("options") or {})
                fmt = str(body.get("format") or "") or None
                if fmt not in (None, "versus", "toplist", "short"):
                    raise FactoryError(f"unknown format '{fmt}'")
                job_id = create_topic_job(s, str(body.get("topic") or ""), vs=str(body.get("vs") or "") or None, sources=sources, fmt=fmt, count=int(body.get("count") or 0) or None, options=options)
                spawn_runner(s, job_id)
                self._json({"id": job_id}, 201)
                return
            if parts == ["api", "jobs"]:
                from ..core.pipeline import create_job

                urls = [u for u in (body.get("urls") or []) if isinstance(u, str) and re.match(r"^https?://", u.strip())]
                if not urls:
                    raise FactoryError("Give at least one http(s) article URL.")
                options = {k: v for k, v in (body.get("options") or {}).items() if k in ("force", "no_search", "audit", "whisper", "allow_broll", "target_seconds", "today")} | voice_options(body.get("options") or {})
                job_id = create_job(s, urls, fmt=str(body.get("format") or "long"), options=options)
                spawn_runner(s, job_id)
                self._json({"id": job_id}, 201)
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and JOB_ID.match(parts[2]) and parts[3] == "resume":
                from ..core.pipeline import ALL_STEP_NAMES

                step = body.get("from_step")
                if step and step not in ALL_STEP_NAMES:
                    raise FactoryError(f"unknown step '{step}'")
                spawn_runner(s, parts[2], from_step=step, force=bool(body.get("force")))
                self._json({"ok": True})
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and JOB_ID.match(parts[2]) and parts[3] == "rewrite":
                from ...modules.script_generation.writer import rewrite_section
                from ...providers.llm import get_llm

                root = s.jobs_dir / parts[2]
                script = read_json(root / "script" / "script.json")
                if not script:
                    raise FactoryError("This job has no script yet.")
                fc = read_json(root / "research" / "fact_check.json", [])
                if script.get("kind") == "topic":
                    from ...modules.topic.produce import job_facts

                    fc = job_facts(s, parts[2], fc)
                quotes = (read_json(root / "research" / "analysis.json", {}) or {}).get("quotes") or []
                script = rewrite_section(get_llm(s), script=script, section_id=str(body.get("section")), instruction=str(body.get("instruction") or ""), fact_check=fc, quotes=quotes)
                write_json(root / "script" / "script.json", script)
                write_script_md(s, parts[2], script, fc)
                self._json({"script": script})
            else:
                self._error(404, "not found")
        except FactoryError as exc:
            self._error(400, exc.message, exc.hint)
        except KeyError as exc:
            self._error(400, str(exc))

    def do_PUT(self) -> None:
        if not self._guard(True):
            return
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        s = self.settings
        try:
            if parts == ["api", "footage", "tags"]:
                from ...modules.footage import library as lib

                body = self._body()
                path = self._footage_file(str(body.get("file") or ""))
                if not path.is_file():
                    raise FactoryError("unknown footage file")
                tags = body.get("tags") or []
                tags = [x for x in (tags.split(",") if isinstance(tags, str) else tags) if str(x).strip()]
                at = body.get("at")
                at = (float(at[0]), float(at[1])) if isinstance(at, list) and len(at) == 2 else None
                side = lib.set_tags(s, path, replace=[str(x) for x in tags], at=at)
                self._json({"ok": True, "sidecar": side})
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and JOB_ID.match(parts[2]) and parts[3] == "script":
                from ...modules.script_generation.writer import word_count

                root = s.jobs_dir / parts[2]
                script = read_json(root / "script" / "script.json")
                if not script:
                    raise FactoryError("This job has no script yet.")
                edits = {e.get("id"): e.get("narration") for e in self._body().get("sections") or [] if isinstance(e, dict)}
                for sec in script["sections"]:
                    if isinstance(edits.get(sec["id"]), str) and edits[sec["id"]].strip():
                        sec["narration"] = re.sub(r"\s+", " ", edits[sec["id"]]).strip()
                        sec["words"] = word_count(sec["narration"])
                        sec["edited_by_operator"] = True
                script["word_budget"]["actual"] = sum(x["words"] for x in script["sections"])
                write_json(root / "script" / "script.json", script)
                fc = read_json(root / "research" / "fact_check.json", [])
                if script.get("kind") == "topic":
                    from ...modules.topic.produce import job_facts

                    fc = job_facts(s, parts[2], fc)
                write_script_md(s, parts[2], script, fc)
                self._json({"script": script})
            else:
                self._error(404, "not found")
        except FactoryError as exc:
            self._error(400, exc.message, exc.hint)

    def do_DELETE(self) -> None:
        if not self._guard(True):
            return
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if parts == ["api", "footage"]:
            from ...modules.footage import library as lib

            try:
                path = self._footage_file((parse_qs(url.query).get("file") or [""])[0])
                if not path.is_file():
                    raise FactoryError("unknown footage file")
                lib.remove_file(self.settings, path)
                self._json({"ok": True})
            except FactoryError as exc:
                self._error(400, exc.message, exc.hint)
        elif len(parts) == 3 and parts[:2] == ["api", "jobs"] and JOB_ID.match(parts[2]):
            from ..core.pipeline import delete_job

            delete_job(self.settings, parts[2])
            self._json({"ok": True})
        else:
            self._error(404, "not found")


def make_server(settings: Any, host: str = "127.0.0.1", port: int = 8600) -> ThreadingHTTPServer:
    allowed = set(LOOPBACK) | ({host} if host not in ("0.0.0.0", "", "::") else set())
    handler = type("BoundHandler", (Handler,), {"settings": settings, "allowed_hosts": frozenset(allowed)})
    return ThreadingHTTPServer((host, port), handler)


def serve(settings: Any, host: str = "127.0.0.1", port: int = 8600) -> None:
    httpd = make_server(settings, host, port)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: the dashboard is listening on {host}; anyone on that network can start jobs.")
    print(f"dashboard: http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
