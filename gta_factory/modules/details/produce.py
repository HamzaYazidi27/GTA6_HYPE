"""Turn picked details into a "Details you missed" video: script, storyboard, thumbnails, review notes.

The narration only says what the vision model saw *and confirmed* in each crop
(plus a short, clearly hedged "why it is interesting"); it never presents a guess
as a fact, and words like "confirmed" or "official" are refused. Every detail is
shown with its timestamp in the original footage so viewers can check it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...app.core.utils import normalize_ws
from ...providers.llm import LLMProvider
from .find import output_window

CERTAIN = re.compile(r"\b(confirm(s|ed|ation)?|official(ly)?|proves?|proof|definitely|100 ?%)\b", re.I)
BANNED = re.compile(r"\b(leak(ed|s)?|datamine[ds]?|insane|shocking|you won'?t believe|exposed)\b", re.I)

# words per part, by layout (16:9 long-form vs 9:16 Short)
BUDGET = {
    "short": {"hook": (8, 16), "line": (9, 18), "outro": (6, 14)},
    "long": {"hook": (14, 30), "line": (22, 40), "outro": (8, 20)},
}

SYSTEM = """You write the narration of "details you missed" videos for an independent GTA VI channel.
You only describe what is listed for each detail: what is visible and any readable text. You never invent
facts about the game, its story or its release. Any interpretation is short and clearly hedged
("could", "maybe", "looks like"). Never use the words confirmed, official, proof, leaked."""

PROMPT = """Video: {n} details from {source}, {layout}.

DETAILS (id | where in the footage | label | what is visible | readable text)
{rows}

Write, in English, for a voice-over:
- "title": the video title, max 70 characters, e.g. "{n} details you missed in {short}".
- "hook": {hmin}-{hmax} words. Promise the {n} details, make the viewer look closer. No question.
- "lines": one per detail, same ids, {lmin}-{lmax} words each. Start by telling the viewer where to look
  ("Look at the sign above the gas station..."), say what is there (quote readable text exactly),
  then at most one hedged sentence on why it is interesting. Keep the order of the ids.
  Each line also gets "label": 2-4 words for the screen (e.g. "GAS STATION SIGN").
- "outro_question": {omin}-{omax} words, one question that makes viewers comment (e.g. which detail they had missed).
- "thumb_lines": 1-3 thumbnail lines, max 12 characters each (e.g. ["{n} DETAILS", "YOU MISSED"]).

Answer with JSON only:
{{"title": "...", "hook": "...", "lines": [{{"id": "D1", "line": "...", "label": "..."}}], "outro_question": "...", "thumb_lines": ["..."]}}"""


def layout_of(settings: Any, fmt: str) -> str:
    return "short" if settings.format(fmt).get("aspect") == "9:16" else "long"


def _words(s: str) -> int:
    return len((s or "").split())


def _fallback(details: list[dict[str, Any]], source: str, n: int) -> dict[str, Any]:
    lines = []
    for d in details:
        where = f"at {d['timecode']} in {d['short_title']}"
        text = f' It reads "{d["text"]}".' if d.get("text") else ""
        lines.append({"id": d["id"], "line": f"Look closer {where}. {d['what'].rstrip('.')}.{text}", "label": d["label"]})
    return {
        "title": f"{n} details you missed in {source}"[:70],
        "hook": f"You watched {source}. But did you see these {n} details?",
        "lines": lines,
        "outro_question": "Which of these details had you missed?",
        "thumb_lines": [f"{n} DETAILS", "YOU MISSED"],
    }


def write_script(llm: LLMProvider | None, settings: Any, details: list[dict[str, Any]], *, fmt: str, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """The narration: hook, one line per detail, outro question (``script.json``-compatible)."""
    layout = layout_of(settings, fmt)
    b = BUDGET[layout]
    sources = list(dict.fromkeys(d["short_title"] for d in details))
    source = sources[0] if len(sources) == 1 else "the GTA 6 trailers"
    n = len(details)
    rows = "\n".join(f"{d['id']} | {d['short_title']} at {d['timecode']} | {d['label']} | {d['what']} | {d.get('text') or '-'}" for d in details)
    ids = [d["id"] for d in details]

    def validate(data: Any) -> str | None:
        if not isinstance(data, dict):
            return "answer must be an object"
        lines = {str(x.get("id")): x for x in data.get("lines") or [] if isinstance(x, dict)}
        missing = [i for i in ids if not str((lines.get(i) or {}).get("line") or "").strip()]
        if missing:
            return f"a line is missing for: {', '.join(missing)}"
        for key in ("title", "hook", "outro_question"):
            if not str(data.get(key) or "").strip():
                return f"'{key}' is empty"
        spoken = " ".join([str(data["hook"]), str(data["outro_question"])] + [str(lines[i]["line"]) for i in ids])
        if CERTAIN.search(spoken) or BANNED.search(spoken + " " + str(data["title"])):
            return "do not use the words confirmed, official, proof, definitely, leaked or shock wording"
        long = [i for i in ids if _words(str(lines[i]["line"])) > b["line"][1] * 1.35]
        if long:
            return f"lines too long (max {b['line'][1]} words): {', '.join(long)}"
        return None

    data: dict[str, Any] | None = None
    if llm is not None:
        try:
            data = llm.complete_json(
                SYSTEM,
                PROMPT.format(n=n, source=source, short=source, layout="a vertical YouTube Short" if layout == "short" else "a 16:9 YouTube video", rows=rows, hmin=b["hook"][0], hmax=b["hook"][1], lmin=b["line"][0], lmax=b["line"][1], omin=b["outro"][0], omax=b["outro"][1]),
                max_tokens=3000,
                label="details_script",
                validate=validate,
            )
        except Exception as exc:  # the video must still be made: plain lines from what the model saw
            log(f"details script: model failed ({getattr(exc, 'message', exc)}); using plain lines")
    if not data or validate(data):
        data = _fallback(details, source, n)
    by_id = {str(x.get("id")): x for x in data["lines"] if isinstance(x, dict)}
    sections = [{"id": "hook", "label": "Hook", "narration": normalize_ws(str(data["hook"])), "claim_ids": []}]
    for d in details:
        x = by_id[d["id"]]
        label = re.sub(r"\s+", " ", str(x.get("label") or d["label"])).strip().upper()[:28] or d["label"].upper()[:28]
        sections.append({"id": d["id"], "label": f"Detail {d['id'][1:]} - {label}", "screen_label": label, "narration": normalize_ws(str(x["line"])), "claim_ids": []})
    sections.append({"id": "cta", "label": "Outro", "narration": normalize_ws(str(data["outro_question"])), "claim_ids": []})
    for s in sections:
        s["words"] = _words(s["narration"])
    thumb = [re.sub(r"\s+", " ", str(t)).strip().upper()[:12] for t in data.get("thumb_lines") or [] if str(t).strip() and not BANNED.search(str(t))][:3] or [f"{n} DETAILS", "YOU MISSED"]
    title = normalize_ws(str(data["title"]))[:100]
    return {
        "kind": "details",
        "title": title,
        "format": fmt,
        "angle": {"thesis": f"{n} small details from {source} that most viewers missed, each shown at its timestamp in the footage."},
        "sections": sections,
        "thumb_lines": thumb,
        "word_budget": {"target": b["hook"][1] + b["outro"][1] + n * b["line"][1], "actual": sum(s["words"] for s in sections)},
        "quality": {"audit_verdict": "details", "remaining_lint_issues": [], "remaining_audit_issues": []},
    }


def script_markdown(script: dict[str, Any], details: list[dict[str, Any]]) -> str:
    by_id = {d["id"]: d for d in details}
    out = [f"# {script['title']}", "", f"- **Format:** {script['format']} (details you missed)", f"- **Words:** {script['word_budget']['actual']}", ""]
    for s in script["sections"]:
        out += [f"## {s['label']}", "", s["narration"], ""]
        d = by_id.get(s["id"])
        if d:
            out += [f"_{d['video_title']} à {d['timecode']} - vu : {d['what']}" + (f" - texte : « {d['text']} »" if d.get("text") else "") + "_", ""]
    return "\n".join(out)


def build_scenes(script: dict[str, Any], details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hook (kinetic) -> one ``detail`` scene per detail (freeze, zoom, ring, label) -> outro question."""
    by_id = {d["id"]: d for d in details}
    n = len(details)
    scenes: list[dict[str, Any]] = []
    sources = list(dict.fromkeys(d["short_title"] for d in details))

    def add(section: str, narration: str, template: str, data: dict[str, Any], **extra: Any) -> dict[str, Any]:
        sc = {
            "scene_number": len(scenes) + 1,
            "section": section,
            "narration": narration,
            "template": template,
            "data": data,
            "emphasis": "",
            "on_screen_text": extra.pop("screen", ""),
            "visual_description": extra.pop("visual", ""),
            "transition": extra.pop("transition", "cut"),
            "sound_effect": extra.pop("sfx", "none"),
            "claim_ids": [],
            "status": None,
            "source_reference": "",
            "background": "rays" if section == "hook" else "grid",
            **extra,
        }
        scenes.append(sc)
        return sc

    hook = script["sections"][0]
    lines = [f"{n} DETAILS", "YOU MISSED"]
    add("hook", hook["narration"], "kinetic", {"lines": lines, "accent": "MISSED", "kicker": (sources[0] if len(sources) == 1 else "GTA 6")[:44]}, screen=" ".join(lines), visual="Kinetic hook over the footage.", transition="flash", sfx="impact")
    for s in script["sections"][1:-1]:
        d = by_id[s["id"]]
        data = {"n": int(d["id"][1:]), "total": n, "label": s.get("screen_label") or d["label"].upper(), "text": d.get("text") or "", "source": f"{d['short_title'].upper()} · {d['timecode']}"}
        add(
            "story",
            s["narration"],
            "detail",
            data,
            screen=f"#{data['n']} {data['label']}" + (f" « {data['text']} »" if data["text"] else ""),
            visual=f"Freeze on {d['video_title']} at {d['timecode']}, zoom and ring on: {d['what']}",
            sfx="whoosh",
            footage={"kind": "detail", "file": d["path"], "t": d["t"], "shot_s": d["shot_s"], "shot_e": d["shot_e"], "src_w": d["src_w"], "src_h": d["src_h"], "box_src": d["box"], "credit": d["credit"], "detail_id": d["id"]},
        )
    outro = script["sections"][-1]
    add("cta", outro["narration"], "outro", {"question": outro["narration"][:110], "cta": "Tell us in the comments"}, screen=outro["narration"], visual="Closing question.", transition="slice", sfx="swell")
    return scenes


def detail_plan(sc: dict[str, Any], duration: float, *, width: int, height: int, fps: int, run_in: float = 1.2) -> dict[str, Any]:
    """How a detail scene is cut: the shot plays ``run_in`` s up to the detail's frame, which then freezes.

    Also fills the scene ``data`` with what the ``detail`` template needs: the box in output
    fractions (after the cover crop, which is centred on the detail), the zoom and the freeze time.
    """
    from .find import zoom_for

    fo = sc["footage"]
    win = output_window({"src_w": fo.get("src_w"), "src_h": fo.get("src_h"), "box": fo["box_src"]}, width, height)
    ri = max(0.0, min(run_in, fo["t"] - fo["shot_s"] - 0.05))
    if ri < 0.4:
        ri = 0.0
    n_run = int(round(ri * fps))
    n_total = int(round((duration + 0.2) * fps))
    sc["data"].update(box=win["box"], zoom=zoom_for(win["box"], width, height), freeze=round(n_run / fps, 3))
    return {"file": fo["file"], "t": fo["t"], "ri": ri, "n_run": n_run, "n_total": n_total, "win": win, "w": width, "h": height, "fps": fps}


def render_detail_clip(plan: dict[str, Any], out: Path) -> None:
    from ...app.core.errors import RenderError

    w, h, fps, win = plan["w"], plan["h"], plan["fps"], plan["win"]
    n_run, n_total = plan["n_run"], plan["n_total"]
    chain = f"scale={win['sw']}:{win['sh']},crop={w}:{h}:{win['ox']}:{win['oy']},setsar=1,fps={fps},format=yuv420p"
    cmd = ["ffmpeg", "-y", "-v", "error"]
    parts = []
    if n_run:
        cmd += ["-ss", f"{plan['t'] - plan['ri']:.3f}", "-t", f"{plan['ri'] + 0.3:.3f}", "-i", plan["file"]]
        parts.append(f"[0:v]{chain},trim=end_frame={n_run},setpts=PTS-STARTPTS[a];")
    k = 1 if n_run else 0
    cmd += ["-ss", f"{plan['t']:.3f}", "-i", plan["file"]]
    hold = max(1, n_total - n_run)
    parts.append(f"[{k}:v]trim=end_frame=1,{chain},loop=loop={hold}:size=1:start=0,setpts=N/{fps}/TB,trim=end_frame={hold}[b];")
    graph = "".join(parts) + ("[a][b]concat=n=2:v=1:a=0[out]" if n_run else "[b]null[out]")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part.mp4")
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", str(n_total), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-g", str(fps), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists():
        raise RenderError(f"Could not cut a detail clip ({Path(plan['file']).name} at {plan['t']:.2f}s): {proc.stderr.strip()[-400:]}")
    tmp.rename(out)


def thumbnail_concepts(settings: Any, script: dict[str, Any], details: list[dict[str, Any]], *, out_dir: Path) -> list[dict[str, Any]]:
    """Three thumbnails, each a close-up of one of the strongest details with a neon ring around it."""
    from .find import score

    brand = settings.get("channel.name", "NEON DISPATCH")
    best = sorted(details, key=lambda d: -score(d))[: int(settings.get("thumbnail.concepts", 3))]
    lines = script.get("thumb_lines") or [f"{len(details)} DETAILS", "YOU MISSED"]
    variants = [("stamp", lines, "coral"), ("split", ["LOOK", "CLOSER"], "cyan"), ("stamp", [(details and str(details[0].get("short_title", "GTA 6")).upper()[:12]) or "GTA 6", "DETAILS"], "sun")]
    out = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, d in enumerate(best):
        concept, ls, color = variants[i % len(variants)]
        pic, ring = _closeup(d, out_dir / f"detail_thumb_{i + 1}.jpg")
        accent = ls[-1].split()[-1] if ls and ls[-1].split() else ""
        out.append({
            "props": {"concept": concept, "lines": ls, "accent": accent, "status": None, "motif": "question", "bigText": "", "label": "", "brand": brand, "color": color, "calendar": None, "ring": ring},
            "photo_file": str(pic),
            "detail_id": d["id"],
            "why": f"close-up of: {d['what']}",
            "notes": [],
        })
    return out


def _closeup(d: dict[str, Any], dest: Path) -> tuple[Path, dict[str, float]]:
    """A 1280x720 crop of the detail's frame: the detail right of centre, about a fifth of the width."""
    W, H = d.get("src_w") or 1920, d.get("src_h") or 1080
    x1, y1, x2, y2 = (d["box"][0] * W, d["box"][1] * H, d["box"][2] * W, d["box"][3] * H)
    bw = max(8.0, x2 - x1)
    cw = min(float(W), max(bw / 0.22, W / 2.6))
    ch = cw * 9 / 16
    if ch > H:
        ch, cw = float(H), H * 16 / 9
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    x0 = max(0.0, min(W - cw, cx - 0.72 * cw))  # right of centre: the text sits on the left
    y0 = max(0.0, min(H - ch, cy - 0.5 * ch))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{d['t']:.3f}", "-i", d["path"], "-frames:v", "1", "-vf", f"crop={int(cw)}:{int(ch)}:{int(x0)}:{int(y0)},scale=1280:720", "-q:v", "2", str(dest)], capture_output=True)
    ring = {"x": round((x1 - x0) / cw, 4), "y": round((y1 - y0) / ch, 4), "w": round(bw / cw, 4), "h": round(max(8.0, y2 - y1) / ch, 4)}
    return dest, ring


def review_points(details: list[dict[str, Any]]) -> str:
    rows = [f"    - **#{d['id'][1:]}** {d['video_title']} à **{d['timecode']}** : {d['what']}" + (f" (texte lu : « {d['text']} »)" if d.get("text") else "") for d in details]
    return (
        "\n- [ ] Vérifier chaque détail dans la vidéo source, au timestamp indiqué : le cercle entoure bien ce que dit la narration, et le texte est bien lu "
        "(le modèle de vision peut se tromper ; corrige le script avec le dashboard puis « refaire depuis : Storyboard »).\n" + "\n".join(rows)
    )


def description_block(script: dict[str, Any], details: list[dict[str, Any]]) -> list[str]:
    """Lines for the YouTube description: each detail and where to find it in the original footage."""
    labels = {s["id"]: s.get("screen_label") or s["label"] for s in script.get("sections") or []}
    return [f"#{d['id'][1:]} {labels.get(d['id'], d['label'])} - {d['video_title']} at {d['timecode']}" for d in details]
