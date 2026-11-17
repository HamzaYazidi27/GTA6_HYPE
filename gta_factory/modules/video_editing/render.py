"""Rendering: drive ``templates/render.mjs`` (Remotion) from Python.

* The video is rendered in chunks of whole scenes (about 40 s each). Each chunk
  is cached under a hash of everything that changes its pixels, so editing one
  scene only re-renders the chunk that contains it.
* Chunks are encoded at high quality (``video.chunk_crf``) and joined without
  re-encoding; the final encode happens once, in the mux step.
* Real GTA 6 footage (one pre-cut clip or screenshot per scene) and
  operator / stock images are staged into the bundle's ``public/job_media``
  folder, which the renderer preserves across bundle rebuilds.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import RenderError
from ...app.core.utils import stable_hash, which, write_json

COMPOSITION = {"long": "Video", "short": "Short"}


def templates_dir(settings: Any) -> Path:
    return settings.root / "templates"


def bundle_dir(settings: Any) -> Path:
    return templates_dir(settings) / ".bundle"


def template_hash(settings: Any) -> str:
    """Hash of the template sources (same rule as render.mjs, so both agree on staleness)."""
    h = hashlib.sha1()
    root = templates_dir(settings)
    for p in sorted(root.rglob("*")):
        if any(part in (".bundle", "node_modules") for part in p.relative_to(root).parts) or not p.is_file():
            continue
        if p.suffix in (".ts", ".tsx", ".mjs", ".css", ".json", ".png", ".jpg", ".svg"):
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def check_renderer(settings: Any) -> None:
    if not which("node"):
        raise RenderError("Node.js is not installed.", hint="Install Node 18+ (https://nodejs.org) and run `npm install` in the project folder.")
    if not (settings.root / "node_modules" / "@remotion" / "renderer").exists():
        raise RenderError("Remotion is not installed.", hint="Run `npm install` in the project folder (or `python -m gta_factory setup`).")
    if not which("ffmpeg") or not which("ffprobe"):
        raise RenderError("ffmpeg / ffprobe are not installed.", hint="macOS: `brew install ffmpeg`.")


def stage_media(settings: Any, job_id: str, scenes: list[dict[str, Any]]) -> dict[int, str]:
    """Copy b-roll images into the bundle public dir. Returns scene number -> path for ``staticFile``."""
    out: dict[int, str] = {}
    target = bundle_dir(settings) / "public" / "job_media" / job_id
    for sc in scenes:
        src = sc.get("media_file")
        if not src or sc.get("template") != "broll":
            continue
        p = Path(src)
        if not p.exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        dest = target / f"scene_{sc['scene_number']:03d}{p.suffix.lower()}"
        shutil.copyfile(p, dest)
        out[sc["scene_number"]] = f"job_media/{job_id}/{dest.name}"
    return out


def stage_footage(settings: Any, job_id: str, scenes: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    """Copy the per-scene GTA 6 footage (cut clip or screenshot) into the bundle public dir.

    Returns scene number -> {"src": path for ``staticFile``, "kind": "video"|"photo", "credit": ...}.
    """
    out: dict[int, dict[str, str]] = {}
    target = bundle_dir(settings) / "public" / "job_media" / job_id
    for sc in scenes:
        fo = sc.get("footage") or {}
        src = fo.get("clip") if fo.get("kind") in ("video", "detail") else fo.get("file") if fo.get("kind") == "photo" else None
        if not src or not Path(src).exists():
            continue
        p = Path(src)
        target.mkdir(parents=True, exist_ok=True)
        dest = target / f"footage_{sc['scene_number']:03d}{p.suffix.lower()}"
        if not dest.exists() or dest.stat().st_size != p.stat().st_size:
            shutil.copyfile(p, dest)
        out[sc["scene_number"]] = {"src": f"job_media/{job_id}/{dest.name}", "kind": "photo" if fo["kind"] == "photo" else "video", "credit": fo.get("credit") or ""}
    return out


def plan_chunks(props: dict[str, Any], target_seconds: float = 40.0) -> list[dict[str, Any]]:
    """Group consecutive scenes into chunks of roughly ``target_seconds``."""
    fps = props["fps"]
    chunks: list[dict[str, Any]] = []
    cur: list[dict[str, Any]] = []
    for sc in props["scenes"]:
        cur.append(sc)
        frames = cur[-1]["startFrame"] + cur[-1]["durationFrames"] - cur[0]["startFrame"]
        if frames >= target_seconds * fps:
            chunks.append({"scenes": cur})
            cur = []
    if cur:
        if chunks and (cur[-1]["startFrame"] + cur[-1]["durationFrames"] - cur[0]["startFrame"]) < 8 * fps:
            chunks[-1]["scenes"] += cur  # avoid a tiny last chunk
        else:
            chunks.append({"scenes": cur})
    for c in chunks:
        first, last = c["scenes"][0], c["scenes"][-1]
        c["frameRange"] = [first["startFrame"], last["startFrame"] + last["durationFrames"] - 1]
    return chunks


def run_renderer(
    settings: Any,
    tasks: list[dict[str, Any]],
    *,
    work_dir: Path,
    log: Callable[[str], None] = lambda m: None,
    progress: Callable[[float, str], None] = lambda p, m: None,
) -> list[dict[str, Any]]:
    """Run render.mjs on ``tasks``. Raises :class:`RenderError` with the renderer's message on failure."""
    check_renderer(settings)
    job = {
        "bundleDir": str(bundle_dir(settings)),
        "concurrency": int(settings.get("video.render_concurrency", 0) or 0),
        "browserExecutable": os.environ.get("GTA_FACTORY_BROWSER") or None,
        "tasks": tasks,
    }
    job_file = write_json(work_dir / "render_job.json", job)
    cmd = ["node", str(templates_dir(settings) / "render.mjs"), str(job_file)]
    proc = subprocess.Popen(cmd, cwd=str(settings.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    totals = {i: 1 for i in range(len(tasks))}
    done_frames = {i: 0 for i in range(len(tasks))}
    last_pct = -1.0
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(ev)
        kind = ev.get("event")
        if kind == "bundle" and ev.get("progress") in (0, 1):
            log("renderer: bundle " + ("cached" if ev.get("cached") else "built") if ev.get("progress") == 1 else "renderer: bundling templates ...")
        elif kind == "browser_download":
            progress(0.0, f"downloading the headless browser ({ev.get('progress', 0):.0%})")
        elif kind == "progress":
            i = ev["index"]
            totals[i] = max(1, int(ev.get("total") or 1))
            done_frames[i] = int(ev.get("rendered") or 0)
            pct = sum(done_frames.values()) / max(1, sum(totals.values()))
            if pct - last_pct >= 0.02:
                last_pct = pct
                progress(pct, f"rendered {sum(done_frames.values())}/{sum(totals.values())} frames")
        elif kind == "task_done":
            done_frames[ev["index"]] = totals[ev["index"]]
            log(f"renderer: {Path(ev['out']).name} done in {ev.get('seconds', 0):.1f}s")
        elif kind in ("task_error", "fatal"):
            errors.append(ev.get("message", "unknown renderer error"))
    proc.wait()
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0 or errors:
        detail = (errors[0] if errors else stderr.strip()[-1500:]) or f"exit code {proc.returncode}"
        hint = "Run `npm install`, then retry the render step (`python -m gta_factory resume <job> --from render`)."
        if "browser" in detail.lower() or "chrome" in detail.lower():
            hint = "The headless browser could not start. Retry once (it is downloaded on first use), or set GTA_FACTORY_BROWSER to a Chrome/Chromium executable."
        raise RenderError(f"Remotion render failed: {detail[:1200]}", hint=hint)
    return events


def concat_chunks(chunks: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in chunks), encoding="utf-8")
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists():
        raise RenderError("Could not join the rendered chunks: " + proc.stderr[-600:])
    return out


def render_video(
    settings: Any,
    props: dict[str, Any],
    *,
    work_dir: Path,
    out: Path,
    log: Callable[[str], None] = lambda m: None,
    progress: Callable[[float, str], None] = lambda p, m: None,
) -> dict[str, Any]:
    """Render the silent picture track to ``out``. Returns chunk statistics."""
    composition = COMPOSITION[props["format"]]
    chunk_dir = settings.jobs_dir / ".cache" / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    thash = template_hash(settings)
    crf = int(settings.get("video.chunk_crf", 14))
    shared = {k: v for k, v in props.items() if k != "scenes"}
    chunks = plan_chunks(props)
    files: list[Path] = []
    tasks: list[dict[str, Any]] = []
    reused = 0
    public = bundle_dir(settings) / "public"
    for c in chunks:
        media = {s["n"]: hashlib.sha1((public / s["media"]).read_bytes()).hexdigest() for s in c["scenes"] if s.get("media") and (public / s["media"]).exists()}
        media.update({f"f{s['n']}": hashlib.sha1((public / s["footage"]["src"]).read_bytes()).hexdigest() for s in c["scenes"] if s.get("footage") and (public / s["footage"]["src"]).exists()})
        key = stable_hash({"t": thash, "shared": shared, "scenes": c["scenes"], "media": media, "range": c["frameRange"], "comp": composition, "crf": crf})
        path = chunk_dir / f"chunk_{key}.mp4"
        files.append(path)
        if path.exists() and path.stat().st_size > 0:
            reused += 1
            continue
        tasks.append({"kind": "video", "composition": composition, "props": {"timeline": props}, "frameRange": c["frameRange"], "out": str(path), "crf": crf})
    log(f"render: {len(chunks)} chunk(s), {reused} reused from cache, {len(tasks)} to render")
    if tasks:
        run_renderer(settings, tasks, work_dir=work_dir, log=log, progress=progress)
    concat_chunks(files, out)
    return {"chunks": len(chunks), "reused_chunks": reused, "rendered_chunks": len(tasks), "composition": composition, "template_hash": thash}


def render_stills(settings: Any, stills: list[dict[str, Any]], *, work_dir: Path, log: Callable[[str], None] = lambda m: None) -> None:
    """``stills``: [{"composition": "Thumbnail", "props": {...}, "out": path}]"""
    tasks = [{"kind": "still", "composition": s["composition"], "props": s["props"], "frame": 0, "out": str(s["out"]), "scale": s.get("scale", 1)} for s in stills]
    run_renderer(settings, tasks, work_dir=work_dir, log=log)
