"""Find the small details viewers miss in the GTA 6 footage, with the free local vision model.

For every usable shot of the chosen videos, the middle frame is shown to the
vision model (Ollama, ``footage.vision_model``), which points at up to three
small, easy-to-miss things: readable text (signs, plates, screens, shop names),
brand parodies, odd objects, animals, background characters... each with a
bounding box. Every detail that is about to be used is then **checked**: the crop
alone is shown to the model, which must confirm it really shows what was described
(and re-reads any text). Unconfirmed details are dropped, so the edit never circles
an empty patch of sky. (Checking only the details that would be used keeps a full
trailer scan to about one model call per shot.)

Results are cached in ``assets/gta6_footage/details.json`` (per frame and model):
a video is only ever scanned once, and an interrupted scan resumes.

``pick_details`` then chooses the N best: most interesting first (readable text
counts extra), one per shot, spread over the video, never a detail an earlier
video already showed (``details_shown.json``), in the order they appear in the
footage with the strongest one kept for last.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.errors import FactoryError, ProviderError
from ...app.core.utils import read_json, write_json
from ..footage.library import build_index, footage_dir, load_items

FIND_PROMPT = """This picture is a frame from official Grand Theft Auto VI footage ({source}).
GTA fans love "details you missed" videos. Find up to 3 SMALL, easy-to-miss details in this exact picture
that a fan would find interesting: readable text (signs, shop names, billboards, screens, license plates,
graffiti, headlines), brand parodies and logos, a funny or unusual object, an animal, a character in the
background doing something, a vehicle, a weapon, an easter egg.
Rules:
- Only things you can clearly SEE in the picture. Never guess what is outside the frame.
- Copy any text exactly as written; leave "text" empty if you cannot read it for sure.
- Each detail is small: its box covers less than a quarter of the picture.
- Skip the obvious main subject everyone sees first; we want what viewers miss.

The picture is {w} x {h} pixels. Give each detail's bounding box in pixels: [x1, y1, x2, y2].
Answer with JSON only:
{{"details": [{{"label": "2-5 words", "what": "one sentence: exactly what is visible", "text": "", "box": [x1, y1, x2, y2], "interest": 1-10}}]}}
If there is nothing worth showing, answer {{"details": []}}."""

VERIFY_PROMPT = """This picture is a close-up crop from official Grand Theft Auto VI footage.
Does it clearly show this: "{what}"?{text_q}
Answer with JSON only: {{"visible": true or false, "text": "the exact readable text in the crop, or empty"}}"""

SEND_W = 1008  # Qwen-VL works on 28-pixel patches: 1008 x 560 is read without resampling
MIN_AREA, MAX_AREA = 0.0012, 0.30


def details_path(settings: Any) -> Path:
    return footage_dir(settings) / "details.json"


def shown_path(settings: Any) -> Path:
    return footage_dir(settings) / "details_shown.json"


def video_title(file_rel: str) -> str:
    """``videos/Grand Theft Auto VI Trailer 2 [VQRLujxTm3c].mp4`` -> ``Grand Theft Auto VI Trailer 2``."""
    stem = Path(file_rel).stem
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]|[_]+", " ", stem)).strip() or stem


def short_title(file_rel: str) -> str:
    t = re.sub(r"(?i)^grand theft auto vi\s*[-:|]?\s*", "", video_title(file_rel)).strip()
    return t or "GTA 6"


def timecode(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def send_size(w: int, h: int) -> tuple[int, int]:
    """Size the frame is sent at: 1008 wide (or tall), both sides multiples of 28."""
    if not w or not h:
        return SEND_W, 560
    if w >= h:
        sw, sh = SEND_W, max(28, round(SEND_W * h / w / 28) * 28)
    else:
        sh, sw = SEND_W, max(28, round(SEND_W * w / h / 28) * 28)
    return sw, sh


def parse_box(box: Any, w: int, h: int) -> list[float] | None:
    """A box from the model -> [x1, y1, x2, y2] as fractions of the picture, or None when unusable.

    Accepts pixels of the sent picture, 0-1000 normalised values (older Qwen-VL) or 0-1 fractions.
    """
    if isinstance(box, dict):
        box = [box.get(k) for k in ("x1", "y1", "x2", "y2")]
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    vals = (x1, y1, x2, y2)
    if max(vals) <= 1.0:
        fx = fy = 1.0
    elif max(x1, x2) > w * 1.05 or max(y1, y2) > h * 1.05:
        fx = fy = 1000.0
    else:
        fx, fy = float(w), float(h)
    x1, x2 = sorted((x1 / fx, x2 / fx))
    y1, y2 = sorted((y1 / fy, y2 / fy))
    x1, y1, x2, y2 = (max(0.0, min(1.0, v)) for v in (x1, y1, x2, y2))
    area = (x2 - x1) * (y2 - y1)
    if not (MIN_AREA <= area <= MAX_AREA) or (x2 - x1) < 0.01 or (y2 - y1) < 0.01:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def frame_bytes(path: Path, t: float, *, size: tuple[int, int] | None = None, crop: list[float] | None = None, margin: float = 0.25) -> bytes:
    """A JPEG of one frame: whole (scaled to ``size``) or a crop around a box (+ margin), max 672 px."""
    vf = []
    if crop:
        x1, y1, x2, y2 = crop
        mw, mh = (x2 - x1) * margin, (y2 - y1) * margin
        cx1, cy1 = max(0.0, x1 - mw), max(0.0, y1 - mh)
        cx2, cy2 = min(1.0, x2 + mw), min(1.0, y2 + mh)
        vf.append(f"crop=iw*{cx2 - cx1:.4f}:ih*{cy2 - cy1:.4f}:iw*{cx1:.4f}:ih*{cy1:.4f}")
        vf.append("scale='if(gt(iw,ih),min(672,max(224,iw)),-2)':'if(gt(iw,ih),-2,min(672,max(224,ih)))'")
    elif size:
        vf.append(f"scale={size[0]}:{size[1]}")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", ",".join(vf) or "null", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise ProviderError(f"Could not read a frame of {path.name} at {t:.2f}s: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout


def _clean(s: Any, n: int) -> str:
    t = re.sub(r"\s+", " ", str(s or "")).strip().strip("\"“”'")
    return t[:n].rstrip()


def _match_videos(index: dict[str, Any], wanted: list[str] | None) -> list[dict[str, Any]]:
    vids = index.get("videos", [])
    if not wanted:
        return vids
    out = []
    for v in vids:
        hay = (v["file"] + " " + " ".join(v.get("tags") or [])).lower()
        if any(w.lower() in hay or w.lower().replace(" ", "_") in hay for w in wanted):
            out.append(v)
    return out


def scan(
    settings: Any,
    *,
    videos: list[str] | None = None,
    finder: Callable[[bytes, str], dict[str, Any]] | None = None,
    model: str | None = None,
    limit: int | None = None,
    log: Callable[[str], None] = print,
    progress: Callable[[float, str], None] = lambda f, m: None,
) -> list[dict[str, Any]]:
    """Look for details in every usable shot of the chosen videos (cached). Returns all verified candidates."""
    from ..footage.autotag import ollama_tagger, vision_model

    index = build_index(settings, log=log)
    chosen = _match_videos(index, videos)
    if not chosen:
        have = ", ".join(video_title(v["file"]) for v in index.get("videos", [])) or "none"
        raise FactoryError(f"No video of the footage library matches {videos or 'anything'} (videos: {have}).", hint="Add the trailer or gameplay first: `python -m gta_factory footage update` or `footage add <file or URL>`.")
    tag_model = model or vision_model(settings)
    cache_file = details_path(settings)
    cache = read_json(cache_file, {}) or {}
    cache.setdefault("items", {})
    min_len = float(settings.get("details.min_shot_seconds", 1.5))
    frames = []
    for v in chosen:
        for sh in v.get("shots", []):
            if sh.get("ok") and sh["e"] - sh["s"] >= min_len:
                t = round((sh["s"] + sh["e"]) / 2, 2)
                frames.append((v, sh, t, f"{v['file']}|{v.get('size', 0)}|{t:.2f}"))
    todo = [f for f in frames if f[3] not in cache["items"] or cache["items"][f[3]].get("model") != (tag_model if finder is None else "custom")]
    if limit is not None:
        todo = todo[: max(0, limit)]
    if todo:
        call = finder or ollama_tagger(settings, tag_model, max_tokens=700)
        log(f"details: looking at {len(todo)} frame(s) of {len(chosen)} video(s) with {tag_model if finder is None else 'the given model'} (only once: results are cached)")
        for i, (v, sh, t, key) in enumerate(todo):
            path = footage_dir(settings) / v["file"]
            sw, sh_ = send_size(int(v.get("width") or 0), int(v.get("height") or 0))
            found: list[dict[str, Any]] = []
            try:
                answer = call(frame_bytes(path, t, size=(sw, sh_)), FIND_PROMPT.format(source=f"{video_title(v['file'])}, at {timecode(t)}", w=sw, h=sh_))
                raw = answer.get("details") if isinstance(answer, dict) else None
                for d in (raw if isinstance(raw, list) else [])[:3]:
                    if not isinstance(d, dict):
                        continue
                    box = parse_box(d.get("box") or d.get("bbox") or d.get("bbox_2d"), sw, sh_)
                    what = _clean(d.get("what"), 200)
                    label = _clean(d.get("label"), 40)
                    if not box or not what or not label:
                        continue
                    try:
                        interest = max(1, min(10, int(float(d.get("interest") or 5))))
                    except (TypeError, ValueError):
                        interest = 5
                    found.append({"label": label, "what": what, "text": _clean(d.get("text"), 60), "box": box, "interest": interest, "verified": None})
            except ProviderError as exc:
                log(f"details: {video_title(v['file'])} at {timecode(t)}: {exc.message}")
                continue
            cache["items"][key] = {"model": tag_model if finder is None else "custom", "details": found}
            for d in found:
                log(f"  {short_title(v['file'])} {timecode(t)}: {d['label']}" + (f" « {d['text']} »" if d["text"] else ""))
            progress((i + 1) / len(todo), f"details {i + 1}/{len(todo)}")
            if (i + 1) % 5 == 0:
                write_json(cache_file, cache)  # resumable
        write_json(cache_file, cache)
    return candidates(settings, index, chosen, cache)


def make_verifier(settings: Any, *, finder: Callable[[bytes, str], dict[str, Any]] | None = None, model: str | None = None, log: Callable[[str], None] = print) -> Callable[[dict[str, Any]], bool]:
    """``verify(candidate) -> bool``: the close-up check, asked once per detail and cached in details.json."""
    from ..footage.autotag import ollama_tagger, vision_model

    state: dict[str, Any] = {}

    def verify(d: dict[str, Any]) -> bool:
        cache = state.setdefault("cache", read_json(details_path(settings), {}) or {"items": {}})
        entry = (cache.get("items") or {}).get(d["frame_key"]) or {}
        dets = entry.get("details") or []
        det = dets[d["k"]] if d["k"] < len(dets) else None
        if det is not None and det.get("verified") is not None:
            if det.get("text") != d.get("text"):
                d["text"] = det.get("text") or ""
            return bool(det["verified"])
        if "call" not in state:
            state["call"] = finder or ollama_tagger(settings, model or vision_model(settings), max_tokens=200)
        q = f' It should contain the text "{d["text"]}".' if d.get("text") else ""
        try:
            check = state["call"](frame_bytes(Path(d["path"]), d["t"], crop=d["box"]), VERIFY_PROMPT.format(what=d["what"], text_q=q))
        except ProviderError as exc:
            log(f"details: could not check {d['label']} ({exc.message})")
            return False
        ok = isinstance(check, dict) and check.get("visible") in (True, "true", "yes", 1)
        seen = _clean(check.get("text"), 60) if isinstance(check, dict) else ""
        if ok and d.get("text") and seen and seen.lower() != d["text"].lower():
            d["text"] = seen  # the close-up reads better than the whole frame
        log(f"  check {d['short_title']} {d['timecode']} {d['label']}: {'ok' if ok else 'not visible, dropped'}")
        if det is not None:
            det["verified"], det["text"] = ok, d.get("text") or ""
            write_json(details_path(settings), cache)
        return ok

    return verify


def candidates(settings: Any, index: dict[str, Any], chosen: list[dict[str, Any]], cache: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every verified detail of the chosen videos, with what is needed to cut and narrate it."""
    cache = cache or read_json(details_path(settings), {}) or {}
    items = load_items(settings, index)
    shot_tags = {(it["file"], it["s"]): sorted(it["tags"]) for it in items["shots"]}
    root = footage_dir(settings)
    out = []
    for v in chosen:
        for sh in v.get("shots", []):
            t = round((sh["s"] + sh["e"]) / 2, 2)
            entry = (cache.get("items") or {}).get(f"{v['file']}|{v.get('size', 0)}|{t:.2f}") or {}
            for k, d in enumerate(entry.get("details") or []):
                if d.get("verified") is False:
                    continue
                out.append({
                    "key": f"{v['file']}|{t:.2f}|{k}",
                    "frame_key": f"{v['file']}|{v.get('size', 0)}|{t:.2f}",
                    "k": k,
                    "file": v["file"],
                    "path": str(root / v["file"]),
                    "video_title": video_title(v["file"]),
                    "short_title": short_title(v["file"]),
                    "t": t,
                    "timecode": timecode(t),
                    "shot_s": sh["s"],
                    "shot_e": sh["e"],
                    "src_w": int(v.get("width") or 0),
                    "src_h": int(v.get("height") or 0),
                    "credit": v.get("credit") or "Footage: Rockstar Games",
                    "source_url": v.get("source_url") or "",
                    "tags": shot_tags.get((v["file"], sh["s"]), []),
                    **d,
                })
    return out


def score(d: dict[str, Any]) -> float:
    return d["interest"] + (2.0 if d.get("text") else 0.0) + (0.5 if any(t in ("lucia", "jason") for t in d.get("tags") or []) else 0.0)


def pick_details(settings: Any, cands: list[dict[str, Any]], count: int, *, verify: Callable[[dict[str, Any]], bool] | None = None) -> list[dict[str, Any]]:
    """The ``count`` best details: one per shot, at least 4 s apart, not shown in an earlier video, best one last.

    With ``verify``, each detail is checked in close-up before it is kept (failures are skipped).
    """
    shown = read_json(shown_path(settings), {}) or {}
    order = {f: i for i, f in enumerate(dict.fromkeys(d["file"] for d in cands))}
    ranked = sorted(cands, key=lambda d: (d["key"] in shown, -score(d), order[d["file"]], d["t"]))
    picked: list[dict[str, Any]] = []
    labels: set[str] = set()
    for d in ranked:
        if len(picked) >= count:
            break
        if any(p["file"] == d["file"] and (abs(p["t"] - d["t"]) < 4.0 or p["shot_s"] == d["shot_s"]) for p in picked):
            continue
        lab = re.sub(r"[^a-z0-9]", "", (d.get("text") or d["label"]).lower())
        if lab in labels:
            continue
        if verify is not None and not verify(d):
            continue
        labels.add(re.sub(r"[^a-z0-9]", "", (d.get("text") or d["label"]).lower()))
        picked.append(d)
    if not picked:
        return []
    best = max(picked, key=score)
    rest = sorted((p for p in picked if p is not best), key=lambda d: (order[d["file"]], d["t"]))
    return [{**d, "id": f"D{i}"} for i, d in enumerate(rest + [best], 1)]


def remember_shown(settings: Any, details: list[dict[str, Any]], job_id: str) -> None:
    shown = read_json(shown_path(settings), {}) or {}
    for d in details:
        shown.setdefault(d["key"], [])
        if job_id not in shown[d["key"]]:
            shown[d["key"]].append(job_id)
    write_json(shown_path(settings), shown)


def output_window(d: dict[str, Any], w: int, h: int) -> dict[str, Any]:
    """How a detail's frame fills a ``w`` x ``h`` output: cover-scaled, cropped around the detail (9:16 keeps it in view).

    Returns {"sw", "sh", "ox", "oy"} (scaled size and crop offset, pixels) and "box" in output fractions.
    """
    W, H = d.get("src_w") or w, d.get("src_h") or h
    s = max(w / W, h / H)
    sw, sh = int(math.ceil(W * s / 2) * 2), int(math.ceil(H * s / 2) * 2)
    x1, y1, x2, y2 = d["box"]
    cx, cy = (x1 + x2) / 2 * sw, (y1 + y2) / 2 * sh
    ox = int(max(0, min(sw - w, cx - w / 2)))
    oy = int(max(0, min(sh - h, cy - h / 2)))
    box = [max(0.0, min(1.0, (x1 * sw - ox) / w)), max(0.0, min(1.0, (y1 * sh - oy) / h)), max(0.0, min(1.0, (x2 * sw - ox) / w)), max(0.0, min(1.0, (y2 * sh - oy) / h))]
    return {"sw": sw, "sh": sh, "ox": ox, "oy": oy, "box": [round(v, 4) for v in box]}


def zoom_for(box: list[float], w: int, h: int) -> float:
    """Zoom that makes the detail big but leaves room for the label card and the captions (1.4x to 3.2x: more only blurs)."""
    bw, bh = max(1.0, (box[2] - box[0]) * w), max(1.0, (box[3] - box[1]) * h)
    fw, fh = (0.6, 0.2) if h > w else (0.45, 0.3)
    return round(max(1.4, min(3.2, fw * w / bw, fh * h / bh)), 2)
