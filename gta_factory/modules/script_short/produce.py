"""Script to Short: the pipeline steps of a job made from a pasted narration script.

    script_plan  Analyze the script: beats, list numbers, what each phrase must show, claims
    claims       Fact-check the claims against the news search and the official pages
    voice        Generate the narration (Kokoro, one take per sentence)
    sync         Word-level alignment: when every word, and so every beat, is spoken
    footage      Find footage: library, new official Rockstar videos, CLIP frame index
    match        Match each beat to the moment of real gameplay that shows it (frame-verified);
                 beats the library cannot show are searched on the web (YouTube, then pictures),
                 and what is still missing gets the closest footage there is: never a blank screen
    edit         Build the timeline and apply the editing style (cuts, zoom, grade, captions, SFX)
    render / mix  (shared with the other videos)
    cover        Thumbnail from the best verified frame
    metadata     Title, description, hashtags
    qc           Sync, authenticity, rights, claims + the technical checks
    package      MP4, narration, SRT, EDL, footage manifest, report

Job folder additions: ``plan/edit_plan.json``, ``research/claims.json``, ``sync/beats.json``,
``footage/library_status.json``, ``match/matches.json``, ``edit/`` (EDL, manifest, SFX events),
``qc/sync_report.json`` and ``qc/production_report.md``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...app.core.errors import ConfigError, FactoryError
from ...app.core.utils import read_json, slugify, stable_hash, write_json, write_text
from .plan import plan_script, sentences_of
from .style import editor_params, load_profile

SCRIPT_STEPS: list[tuple[str, str]] = [
    ("script_plan", "Analyze the script"),
    ("claims", "Fact-check the claims"),
    ("sync", "Align every word of the narration"),
    ("footage", "Find footage"),
    ("match", "Match the footage to each phrase"),
    ("edit", "Build the timeline and apply the editing style"),
    ("cover", "Cover image"),
    ("short_metadata", "Title, description, hashtags"),
    ("short_qc", "Quality control (sync, authenticity, rights)"),
    ("short_package", "Package the Short"),
]
SCRIPT_STEP_NAMES = ["script_plan", "claims", "voice", "sync", "footage", "match", "edit", "render", "mix", "cover", "short_metadata", "short_qc", "short_package"]
MIN_WORDS, MAX_WORDS = 8, 260


class Files:
    """The Script-to-Short files of one job folder."""

    def __init__(self, root: Path):
        self.root = root

    source = property(lambda self: self.root / "script" / "source_script.txt")
    plan = property(lambda self: self.root / "plan" / "edit_plan.json")
    claims = property(lambda self: self.root / "research" / "claims.json")
    beats = property(lambda self: self.root / "sync" / "beats.json")
    library = property(lambda self: self.root / "footage" / "library_status.json")
    matches = property(lambda self: self.root / "match" / "matches.json")
    edl = property(lambda self: self.root / "edit" / "edit_decision_list.edl")
    edl_json = property(lambda self: self.root / "edit" / "edit_decision_list.json")
    manifest = property(lambda self: self.root / "edit" / "footage_manifest.json")
    sfx = property(lambda self: self.root / "edit" / "sfx_events.json")
    style = property(lambda self: self.root / "edit" / "style_used.json")
    sync_report = property(lambda self: self.root / "qc" / "sync_report.json")
    report = property(lambda self: self.root / "qc" / "production_report.md")
    cover = property(lambda self: self.root / "thumbnails" / "cover.png")


def files(ctx: Any) -> Files:
    return Files(ctx.paths.root)


def _llm(ctx: Any) -> Any:
    """The job's model, or None when it is not reachable (the rules then plan the edit)."""
    if ctx.options.get("llm") == "none":
        return None
    try:
        return ctx.llm
    except FactoryError as exc:
        ctx.warn(f"no language model ({exc.describe()}): the rule-based plan is used")
        return None


def check_script(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    n = len(text.split())
    if n < MIN_WORDS:
        raise ConfigError(f"The script is too short ({n} words): paste the full narration of the Short.")
    if n > MAX_WORDS:
        raise ConfigError(f"The script has {n} words: a Short reads at most about {MAX_WORDS} (one minute). Cut it down.")
    return text


def create_script_job(settings: Any, script: str, *, options: dict[str, Any] | None = None) -> str:
    """A finished vertical Short from a narration script (``python -m gta_factory short-from-script``)."""
    from ...app.core.pipeline import create_job

    text = check_script(script)
    opts = {**(options or {}), "kind": "script", "script_text": text}
    label = "script:" + (slugify(" ".join(text.split()[:8]))[:40] or "short")
    return create_job(settings, [label], fmt="script_short", options=opts)


# =========================================================================== script_plan
def voice_units(beats: list[dict[str, Any]], words: list[str]) -> list[tuple[int, int, list[int]]]:
    """Sentences read in one take, merged when a beat runs across a sentence end: [(first, last, beat indices)]."""
    units: list[list[Any]] = []
    for a, b in sentences_of(words):
        if units and any(bt["words"][0] <= units[-1][1] < bt["words"][1] for bt in beats):
            units[-1][1] = b  # a beat crosses the previous sentence end: one take
        else:
            units.append([a, b])
    out = []
    for a, b in units:
        idx = [i for i, bt in enumerate(beats) if a <= bt["words"][0] <= b]
        out.append((a, b, idx))
    return out


def number_items(beats: list[dict[str, Any]]) -> None:
    """Each beat knows the list item it belongs to (the badge stays on screen for the whole item)."""
    item = None
    for b in beats:
        if b.get("number"):
            item = b["number"]
        elif b["role"] in ("hook", "suspense", "cta"):
            item = None
        b["item"] = item


def step_script_plan(ctx: Any) -> dict[str, Any]:
    f = files(ctx)
    text = check_script(ctx.options.get("script_text") or "")
    write_text(f.source, text + "\n")
    plan = plan_script(_llm(ctx), text, log=ctx.log)
    beats, words = plan["beats"], plan["words"]
    number_items(beats)
    ed = editor_params(ctx.settings)
    units = voice_units(beats, words)
    scenes = []
    for k, (a, b, idx) in enumerate(units, 1):
        roles = [beats[i]["role"] for i in idx]
        scenes.append({
            "scene_number": k,
            "narration": " ".join(words[a : b + 1]),
            "words": [a, b],
            "beats": idx,
            "template": "shot",
            "section": "cta" if "cta" in roles else "hook" if "hook" in roles else "item" if any(beats[i].get("item") for i in idx) else "body",
            "data": {},
            "background": "grid",
            "transition": "cut",
            "sound_effect": "none",
            "status": None,
            "emphasis": " ".join(e for i in idx for e in beats[i].get("emphasis") or [])[:60],
            "gap_after": float(ed["sentence_gap_s"]),
            "voice_lead": 0.12,
        })
    for b in beats:
        b["scene"] = next(s["scene_number"] for s in scenes if b["words"][0] in range(s["words"][0], s["words"][1] + 1))
    write_json(f.plan, {**plan, "scenes": [{k: s[k] for k in ("scene_number", "words", "beats")} for s in scenes]})
    write_json(ctx.paths.scenes, scenes)
    script = {
        "title": plan["title"],
        "kind": "script",
        "narration": text,
        "sections": [{"id": f"s{s['scene_number']}", "narration": s["narration"]} for s in scenes],
        "angle": {"thesis": plan["title"]},
        "quality": {},
    }
    write_json(ctx.paths.script, script)
    md = [f"# {plan['title']}", "", f"Plan: {plan['method']} - {len(beats)} beats, {len(scenes)} narration takes", ""]
    for i, b in enumerate(beats, 1):
        md.append(f"{i}. **{b['role']}{' #' + str(b['number']) if b.get('number') else ''}** “{b['text']}” -> show: {b['concept']}" + (" (GTA 5)" if b["game"] == "gta5" else "") + ("" if b["specific"] else " (any strong gameplay)"))
    write_text(ctx.paths.script_md, "\n".join(md) + "\n")
    ctx.db.update_job(ctx.job_id, title=plan["title"])
    ctx.log(f"script plan ({plan['method']}): {len(beats)} beats, {sum(1 for b in beats if b['specific'])} need specific footage, numbers {[b['number'] for b in beats if b.get('number')]}")
    return {"method": plan["method"], "beats": len(beats), "specific": sum(1 for b in beats if b["specific"]), "takes": len(scenes), "words": len(words)}


# =========================================================================== claims
def step_claims(ctx: Any) -> dict[str, Any]:
    """Every factual statement of the script, checked against articles (news search + official pages)."""
    from ...app.core.pipeline import load_timeline
    from ..fact_checking.verify import classify, verify_claims

    f = files(ctx)
    plan = ctx.need(f.plan)
    claims = []
    for i, b in enumerate(plan["beats"]):
        if b.get("claim") and b["role"] != "cta":
            claims.append({"id": f"C{len(claims) + 1}", "beat": i, "claim": b["claim"], "type": "fact", "basis": "report", "original_source": "the script", "original_publisher": "the script", "original_tier": "other", "attributed_to": None, "evidence": "", "evidence_verified": False, "importance": 3})
    result: dict[str, Any] = {"claims": [], "sources": [], "status": "checked"}
    if not claims:
        write_json(f.claims, {**result, "status": "no_claims"})
        write_json(ctx.paths.fact_check, [])
        write_text(ctx.paths.fact_check_report, "# Claims\n\nThe script makes no factual claim about the game.\n")
        return {"claims": 0}
    articles: list[Any] = []
    reason = ""
    llm = _llm(ctx)
    if ctx.options.get("no_search"):
        reason = "fact-check search turned off for this job"
    elif llm is None:
        reason = "no language model to read the sources"
    else:
        try:
            from ..topic.research import find_sources

            topic = "GTA 6 " + " ".join(c["claim"] for c in claims[:4])[:160]
            res = find_sources(ctx.settings, topic=topic, style="list", urls=[u for u in ctx.options.get("sources") or []], search=True, log=ctx.log)
            articles = res["articles"]
            if not articles:
                reason = "no article could be found or read (offline, or the search found nothing)"
        except Exception as exc:  # the video is still made; every claim is then marked unverified
            reason = f"source search failed: {type(exc).__name__}: {str(exc)[:160]}"
    checks: dict[str, Any] = {}
    if articles:
        ctx.log(f"claims: checking {len(claims)} claim(s) against {len(articles)} article(s) with the model (one call; a few minutes on a local model)")
        try:
            ver = verify_claims(llm, {"claims": claims}, articles, load_timeline(ctx.settings), ctx.today)
            checks = {c.get("id"): c for c in ver.get("checks") or []}
        except Exception as exc:
            reason = f"verification failed: {type(exc).__name__}: {str(exc)[:160]}"
    fact_check = []
    for c in claims:
        chk = checks.get(c["id"]) or {"sources": []}
        cl = classify(c, chk)
        supports = cl["corroborating_sources"]
        status = "contradicted" if cl["contradicting_sources"] else "supported" if supports else "unverified"
        cl.update(beat=c["beat"], status=status, classification=("confirmed" if any(s["tier"] == "primary" for s in supports) else "reported") if status == "supported" else "unverified" if status == "unverified" else cl["classification"])
        if status == "unverified":
            cl["reasons"] = [reason or "No source found that states it."]
        fact_check.append(cl)
    result.update(claims=fact_check, sources=[{"publisher": a.publisher, "title": a.title, "url": a.url, "tier": a.source_tier} for a in articles], reason=reason)
    if reason and not articles:
        result["status"] = "not_checked"
    write_json(f.claims, result)
    write_json(ctx.paths.fact_check, fact_check)
    write_text(ctx.paths.fact_check_report, claims_report(result))
    counts = {k: sum(1 for c in fact_check if c["status"] == k) for k in ("supported", "unverified", "contradicted")}
    ctx.log("claims: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + (f" ({reason})" if reason else ""))
    return {**counts, "sources": len(articles)}


def claims_report(result: dict[str, Any]) -> str:
    out = ["# Claims of the script", ""]
    if result.get("reason"):
        out += [f"**Note:** {result['reason']}", ""]
    for c in result["claims"]:
        out.append(f"## {c['id']} [{c['status'].upper()}] {c['claim']}")
        for s in c["corroborating_sources"]:
            out.append(f"- supports: [{s['publisher']}]({s['url']}) ({s['tier']}): “{s['evidence'][:200]}”")
        for s in c["contradicting_sources"]:
            out.append(f"- CONTRADICTS: [{s['publisher']}]({s['url']}): “{s['evidence'][:200]}”")
        out += [f"- {r}" for r in c.get("reasons") or []]
        out.append("")
    if result.get("sources"):
        out += ["## Sources read", ""] + [f"- [{s['publisher']}: {s['title']}]({s['url']})" for s in result["sources"]]
    return "\n".join(out) + "\n"


# =========================================================================== sync
def choose_fps(settings: Any, option: Any) -> int:
    """60 when the GTA 6 library's gameplay is 60 fps (``auto``), else 30; or the operator's choice."""
    if str(option) in ("30", "60"):
        return int(option)
    from ..footage.library import build_index, footage_dir
    from .match import source_fps

    vids = [footage_dir(settings) / v["file"] for v in build_index(settings).get("videos", []) if v.get("usable_shots", 1)]
    rates = [source_fps(str(p)) for p in vids if p.exists()]
    return 60 if rates and sum(1 for r in rates if r >= 49) >= 0.9 * len(rates) else int(settings.video_profile("script_short")["fps"])


def beat_times(plan: dict[str, Any], scenes: list[dict[str, Any]], placed: dict[int, dict[str, Any]], scene_words: dict[int, list[dict[str, Any]]], *, cut_lead: float) -> list[dict[str, Any]]:
    """When each beat is on screen: from just before its first word to the next beat's cut.

    ``scene_words``: per scene, its words with times relative to the scene's audio start, one per
    script word of the scene in order.
    """
    out = []
    for sc in scenes:
        p = placed[sc["scene_number"]]
        start, end = p["start_time"], p["start_time"] + p["duration"]
        base = p["start_time"] + p["voice_offset"]
        words = scene_words[sc["scene_number"]]
        a0 = sc["words"][0]
        starts = []
        for k, bi in enumerate(sc["beats"]):
            b = plan["beats"][bi]
            w = words[b["words"][0] - a0]
            starts.append(start if k == 0 else max(start, round(base + w["s"] - cut_lead, 3)))
        for k, bi in enumerate(sc["beats"]):
            b = plan["beats"][bi]
            s0 = starts[k]
            s1 = starts[k + 1] if k + 1 < len(starts) else end
            ws = words[b["words"][0] - a0 : b["words"][1] - a0 + 1]
            out.append({
                "beat": bi,
                "scene": sc["scene_number"],
                "text": b["text"],
                "start": round(s0, 3),
                "end": round(s1, 3),
                "need": round(s1 - s0, 3),
                "speech_start": round(base + ws[0]["s"], 3),
                "speech_end": round(base + ws[-1]["e"], 3),
                "number_at": round(base + ws[0]["s"], 3) if b.get("number") else None,
                "words": [{"w": w["w"], "s": round(base + w["s"], 3), "e": round(base + w["e"], 3)} for w in ws],
            })
    return out


def step_sync(ctx: Any) -> dict[str, Any]:
    from ..subtitles.align import align_scene, build_captions, to_srt, to_vtt
    from ..voice_generation.engine import build_timeline

    s = ctx.settings
    f = files(ctx)
    scenes = ctx.need(ctx.paths.scenes)
    voice = ctx.need(ctx.paths.voice_manifest)
    plan = ctx.need(f.plan)
    fps = choose_fps(s, ctx.options.get("fps", s.get("script_short.fps", "auto")))
    master = build_timeline(s, scenes, voice, ctx.paths.audio, fps, vertical=True)
    write_json(ctx.paths.master, master)
    by_n = {m["scene_number"]: m for m in voice["scenes"]}
    placed = {p["scene_number"]: p for p in master["scenes"]}
    methods: dict[str, int] = {}
    scene_words: dict[int, list[dict[str, Any]]] = {}
    alignments: dict[int, Any] = {}
    all_words: list[dict[str, Any]] = []
    for i, sc in enumerate(scenes):
        m = by_n[sc["scene_number"]]
        al = align_scene(sc["narration"], m["sentences"], ctx.paths.audio / m["file"], m.get("provider_words") or [], use_whisper=ctx.options.get("whisper", True))
        if len(al["words"]) != sc["words"][1] - sc["words"][0] + 1:
            raise FactoryError(f"Alignment of take {sc['scene_number']} returned {len(al['words'])} words for {sc['words'][1] - sc['words'][0] + 1}.", hint="Resume from the voice step.")
        alignments[sc["scene_number"]] = al
        scene_words[sc["scene_number"]] = al["words"]
        methods[al["method"]] = methods.get(al["method"], 0) + 1
        base = placed[sc["scene_number"]]["start_time"] + placed[sc["scene_number"]]["voice_offset"]
        all_words += [{"w": w["w"], "s": round(w["s"] + base, 3), "e": round(w["e"] + base, 3), "scene": sc["scene_number"]} for w in al["words"]]
        ctx.progress((i + 1) / len(scenes), "")
    cues = build_captions(all_words, max_chars=int((s.get("quality_control") or {}).get("max_caption_chars_per_line", 42)))
    write_json(ctx.paths.words, {"words": all_words, "methods": methods, "alignments": {str(k): v for k, v in alignments.items()}})
    write_json(ctx.paths.cues, cues)
    write_text(ctx.paths.srt, to_srt(cues))
    write_text(ctx.paths.vtt, to_vtt(cues))
    ed = editor_params(s)
    beats = beat_times(plan, scenes, placed, scene_words, cut_lead=float(ed["cut_lead_s"]))
    write_json(f.beats, {"fps": fps, "total_seconds": master["total_seconds"], "beats": beats})
    ctx.log(f"sync: {master['total_seconds']:.1f}s at {fps} fps, {len(all_words)} words ({methods}), {len(beats)} beats, shortest {min(b['need'] for b in beats):.2f}s")
    return {"seconds": master["total_seconds"], "fps": fps, "alignment": methods, "beats": len(beats)}


# =========================================================================== footage
def step_footage(ctx: Any) -> dict[str, Any]:
    """The footage the matcher can use: the library (new official videos fetched first), and its CLIP index."""
    from ..footage.clip import Clip, ensure_clip
    from ..footage.games import ensure_footage, game_settings, library_state
    from .match import Library, source_fps

    s = ctx.settings
    f = files(ctx)
    plan = ctx.need(f.plan)
    games = sorted({b.get("game") or "gta6" for b in plan["beats"]})
    status: dict[str, Any] = {"games": {}, "fetched": [], "notes": []}
    if s.get("script_short.fetch_new_official", True) and not ctx.options.get("offline"):
        try:  # source priority 2: new official Rockstar videos (the library is priority 1)
            from ..footage.library import fetch, find_new_official

            new = find_new_official(s, limit=30, log=ctx.log)
            if new:
                ctx.log(f"footage: {len(new)} new official video(s): " + "; ".join(v["title"][:50] for v in new[:5]))
                got = fetch(s, [v["url"] for v in new[:4]], log=ctx.log)
                status["fetched"] = [str(p.name) for p in got]
        except Exception as exc:
            status["notes"].append(f"new official videos not checked: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
    if not ensure_clip(s, log=ctx.log):
        raise FactoryError("The CLIP model (used to see what each frame shows) could not be downloaded.", hint="Check the internet connection, then resume. The files go to assets/models/clip (about 150 MB, one time).")
    model = Clip.load(s)
    if model is None:
        raise ConfigError("CLIP cannot load: the `tokenizers` package is missing.", hint='Run `pip install -e ".[clip]"` (or `pip install tokenizers`), then resume.')
    for gid in games:
        if gid != "gta6" and not ctx.options.get("offline"):
            try:  # a comparison beat ("GTA 5 stopped at five"): that game's official videos, downloaded once
                ensure_footage(s, gid, log=ctx.log)
            except Exception as exc:
                status["notes"].append(f"the {gid} library could not be filled: {str(exc).splitlines()[0][:160]}")
        try:
            gs = game_settings(s, gid) if gid != "gta6" else s
            state = library_state(s, gid)
        except Exception as exc:
            status["games"][gid] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        lib = Library(s, gid, job=ctx.job_id, clip=model, log=ctx.log)
        fps = sorted({source_fps(it["path"]) for it in lib.items["shots"]})
        status["games"][gid] = {**state, "indexed_frames": len(lib.rows), "indexed_shots": len({r["key"] for r in lib.rows if r["kind"] == "shot"}), "footage_dir": str(getattr(gs, "root", ""))}
        if fps:
            status["games"][gid]["fps"] = fps
        if lib.empty:
            status["notes"].append(f"the {gid} library has no usable gameplay video: its beats are searched on the web (or add footage: `python -m gta_factory footage add ...`{' --game ' + gid if gid != 'gta6' else ''})")
    write_json(f.library, status)
    g6 = status["games"].get("gta6") or {}
    ctx.log(f"footage: GTA 6 library {g6.get('usable_shots', 0)} usable shots, {g6.get('indexed_frames', 0)} indexed frames" + "".join(f"; {n}" for n in status["notes"]))
    return {gid: {k: v.get(k) for k in ("usable_shots", "photos", "indexed_frames")} for gid, v in status["games"].items()}


# =========================================================================== match
USAGE_FILE = "footage_usage.json"
USAGE_JOBS = 25  # how many recent Shorts count when a clip was "already shown"


def usage_path(settings: Any) -> Path:
    return settings.jobs_dir / ".cache" / USAGE_FILE


def load_usage(settings: Any, *, exclude_job: str | None = None) -> dict[str, int]:
    """{shot key: how many of the recent Shorts showed it} (this job's own earlier run left out)."""
    data = read_json(usage_path(settings), {}) or {}
    counts: dict[str, int] = {}
    for row in (data.get("jobs") or [])[-USAGE_JOBS:]:
        if row.get("job") == exclude_job:
            continue
        for k in set(row.get("keys") or []):
            counts[k] = counts.get(k, 0) + 1
    return counts


def record_usage(settings: Any, job: str, matches: list[dict[str, Any]]) -> None:
    """Remember which shots this Short shows, so the next ones pick other footage first."""
    path = usage_path(settings)
    data = read_json(path, {}) or {}
    rows = [r for r in data.get("jobs") or [] if r.get("job") != job]
    keys = sorted({sg["key"] for m in matches for sg in m["segments"] if sg.get("key")})
    rows.append({"job": job, "keys": keys})
    write_json(path, {"jobs": rows[-200:]})


def step_match(ctx: Any) -> dict[str, Any]:
    from ..footage.clip import Clip
    from .match import fill_gaps, match_beats, vision_verifier

    s = ctx.settings
    f = files(ctx)
    plan = ctx.need(f.plan)
    timing = ctx.need(f.beats)
    needs = [b["need"] for b in timing["beats"]]
    beats = [plan["beats"][b["beat"]] for b in timing["beats"]]
    verify = vision_verifier(s, ctx.log) if s.get("script_short.vision_verify", True) else None
    if verify is None:
        ctx.log("match: no local vision model (Ollama) - frames are verified with CLIP's vote only (status verified_clip)")
    clip = Clip.load(s)
    history = load_usage(s, exclude_job=ctx.job_id)
    matches = match_beats(s, beats, needs, job=ctx.job_id, clip=clip, verify=verify, allow_photos=bool(ctx.options.get("allow_photos")), history=history, log=ctx.log)
    web = find_on_the_web(ctx, beats, needs, matches, clip=clip, verify=verify, history=history)
    allow_weak = bool(ctx.options.get("allow_weak"))
    for m, t in zip(matches, timing["beats"]):
        if m["specific"] and not m["segments"] and m.get("weak_segments") and allow_weak:  # the operator accepts uncertain footage
            m.update(segments=m["weak_segments"][:1], status="weak", confidence=m["weak_segments"][0]["prob_mean"])
            m["segments"][0]["end"] = round(min(m["segments"][0]["shot_end"], m["segments"][0]["start"] + t["need"]), 3)
    gaps: dict[str, list[int]] = {"related": [], "filler": []}
    if s.get("script_short.never_blank", True) and not ctx.options.get("allow_blank"):
        gaps = fill_gaps(s, beats, needs, matches, job=ctx.job_id, clip=clip, history=history, log=ctx.log)
        for kind, idx in gaps.items():
            if idx:
                ctx.warn(f"match: {len(idx)} beat(s) show the {'closest footage' if kind == 'related' else 'least shown gameplay'} (nothing verified shows them): " + "; ".join(f"beat {k + 1} “{beats[k]['text'][:40]}”" for k in idx))
    for m, t in zip(matches, timing["beats"]):
        m["beat"] = t["beat"]
        m.update({k: t[k] for k in ("start", "end", "scene")})
        m["role"] = plan["beats"][t["beat"]]["role"]
    write_json(f.matches, {"verification": "vision" if verify else "clip", "matches": matches, "web": web, "gaps": gaps})
    write_json(ctx.paths.rights, rights_review(s, matches))
    record_usage(s, ctx.job_id, matches)
    counts: dict[str, int] = {}
    for m in matches:
        counts[m["status"]] = counts.get(m["status"], 0) + 1
    ctx.log("match: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return counts


def find_on_the_web(ctx: Any, beats: list[dict[str, Any]], needs: list[float], matches: list[dict[str, Any]], *, clip: Any, verify: Any, history: dict[str, int] | None = None) -> dict[str, Any]:
    """The beats the library cannot show: footage searched and downloaded from the web, then matched again.

    Pass 2 looks at videos only (the new ones included); pass 3, for what is still missing, also
    accepts a picture (library screenshots, trailer stills, pictures found on the web).
    """
    from .match import match_beats, used_windows
    from .websource import defaults, search_url, source_missing

    s = ctx.settings
    cfg = defaults(s)
    report: dict[str, Any] = {"enabled": cfg["enabled"], "searched": [], "videos": 0, "photos": 0, "filled": [], "photo_filled": []}
    missing = [k for k, m in enumerate(matches) if not m["segments"]]
    if not missing:
        return report
    if not cfg["enabled"] or ctx.options.get("offline") or ctx.options.get("no_web"):
        report["note"] = "web search off (offline / no_web / script_short.web_footage.enabled)"
    else:
        ctx.log(f"web footage: {len(missing)} beat(s) not in the library - searching the web (YouTube, then pictures)")
        found = source_missing(s, [{**beats[k], "index": k} for k in missing], job=ctx.job_id, log=ctx.log)
        report.update(searched=found["beats"], videos=found["videos"], photos=found["photos"], errors=found["errors"])
        for e in found["errors"][:3]:
            ctx.warn(f"web footage: {e}")

    def again(idx: list[int], *, photos: bool) -> list[int]:
        if not idx:
            return []
        got = match_beats(s, [beats[k] for k in idx], [needs[k] for k in idx], job=ctx.job_id, clip=clip, verify=verify, allow_photos=photos, used=used_windows(matches), history=history, log=ctx.log)
        filled = []
        for k, m in zip(idx, got):
            if m["segments"]:
                m["beat"] = k
                m["web"] = any(sg["rights"] == "web_found" or "/web/" in sg["file"] for sg in m["segments"])
                matches[k] = m
                filled.append(k)
                ctx.log(f"match: beat {k + 1} “{m['text'][:48]}” -> {m['status']} ({m['confidence']:.2f}) {m['segments'][0]['file']}" + (" [web]" if m["web"] else "") + (" [picture]" if m["segments"][0]["kind"] == "photo" else ""))
            else:  # the newest reason; the weak candidates of the library pass stay when this one has none
                weak = m.get("weak_segments") or matches[k].get("weak_segments")
                matches[k] = {**m, "beat": k, **({"weak_segments": weak} if weak else {})}
        return filled

    if report["videos"] or report["photos"]:
        report["filled"] = again(missing, photos=bool(ctx.options.get("allow_photos")))
    if cfg["photo_fallback"]:
        report["photo_filled"] = again([k for k in missing if not matches[k]["segments"]], photos=True)
    for k in missing:
        if not matches[k]["segments"]:
            row = next((r for r in report["searched"] if r.get("beat") == k), None)
            if row is not None:
                matches[k]["reason"] = (matches[k].get("reason") or "") + f" | web: {len(row['videos'])} video(s) and {len(row['photos'])} picture(s) found, none shows it"
            matches[k]["search_url"] = search_url((row or {}).get("queries", [beats[k]["concept"]])[0] if row else beats[k]["concept"])
    return report


def rights_review(settings: Any, matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Every piece of footage used, with where it comes from and whether it may be used."""
    assets, credits = [], []
    for m in matches:
        for k, sg in enumerate(m["segments"]):
            ok = sg["rights"] in ("official", "own", "licensed", "third_party_allowed")
            assets.append({
                "asset_id": f"beat{m['beat'] + 1}_{k + 1}",
                "file": sg["file"], "source_url": sg.get("source_url") or "", "creator": sg.get("creator") or "", "credit": sg.get("credit") or "",
                "rights": sg["rights"], "review_status": "cleared" if ok else "check_before_publishing" if sg["rights"] == "web_found" else "needs_human_review",
                "note": {"official": "Official Rockstar Games footage (channel rights policy: short excerpts with credit).", "own": "Captured by the channel.", "licensed": "Licence on file.", "third_party_allowed": "Third-party footage the operator marked as allowed (permission on file).",
                         "web_found": f"Found automatically on the web ({sg.get('creator') or 'unknown author'}): check you may use it (or replace it) before publishing."}.get(sg["rights"], "Origin or permission not documented: do not publish before checking."),
            })
            if sg.get("credit") and sg["credit"] not in credits:
                credits.append(sg["credit"])
    return {"assets": assets, "credits": credits, "footage": {"mode": "script", "enabled": True}}


# =========================================================================== edit
def scene_sequence(sc: dict[str, Any], placed: dict[str, Any], beats_t: list[dict[str, Any]], by_beat: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """The picture of one narration take: each beat's footage (a blank only when the library is empty)."""
    from .match import sequence_for

    segs: list[dict[str, Any]] = []
    for t in beats_t:
        m = by_beat[t["beat"]]
        offset = t["start"] - placed["start_time"]
        if m["segments"]:
            segs += sequence_for(m, t["need"], offset=offset)
        else:
            segs.append({"t": round(offset, 3), "d": round(t["need"], 3), "type": "blank", "beat": t["beat"]})
    return segs


def cut_sequence(segs: list[dict[str, Any]], total_frames: int, *, out: Path, cache_dir: Path, width: int, height: int, fps: int) -> Path:
    """One silent clip for a take: the segments back to back, cut on exact frames (cached by content)."""
    from ..footage.select import _cover_filter, kenburns_filter

    bounds = [round(sg["t"] * fps) for sg in segs] + [total_frames]
    frames = [max(1, b - a) for a, b in zip(bounds, bounds[1:])]
    frames[-1] = max(1, total_frames - sum(frames[:-1]))
    key = stable_hash({"s": segs, "f": frames, "w": width, "h": height, "fps": fps, "v": 2})
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"short_{key}.mp4"
    if not (cached.exists() and cached.stat().st_size > 0):
        cmd = ["ffmpeg", "-y", "-v", "error"]
        chains = []
        photo_k = 0
        for i, (sg, nf) in enumerate(zip(segs, frames)):
            hold = min(nf - 1, int(round(float(sg.get("hold") or 0) * fps)))
            real = nf - hold
            speed = float(sg.get("speed") or 1.0)
            secs = real * speed / fps + 0.25  # seconds of source read (a slowed shot reads less)
            if sg["type"] == "blank":
                cmd += ["-f", "lavfi", "-t", f"{nf / fps + 0.1:.3f}", "-i", f"color=c=0x0b0d12:s={width}x{height}:r={fps}"]
                vf = f"setsar=1,format=yuv420p"
            elif sg["type"] == "photo":  # a picture never sits still: an eased zoom with a slow drift
                cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{real / fps + 0.25:.3f}", "-i", sg["file"]]
                vf = kenburns_filter(width, height, fps, nf, photo_k)
                photo_k += 1
            else:
                cmd += ["-ss", f"{sg['start']:.3f}", "-t", f"{secs:.3f}", "-i", sg["file"]]
                vf = _cover_filter(width, height, fps, sg.get("crop"))
                if speed < 0.999:  # slow motion: the shot fills its beat while still moving
                    vf = f"setpts=(PTS-STARTPTS)/{speed:.4f}," + vf
            pad = f",tpad=stop_mode=clone:stop={hold}" if hold > 0 else ""
            chains.append(f"[{i}:v]{vf},trim=end_frame={real if sg['type'] != 'blank' else nf},setpts=PTS-STARTPTS{pad}[v{i}];")
        graph = "".join(chains) + "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[out]"
        tmp = cached.with_suffix(".part.mp4")
        cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", str(total_frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-g", str(fps), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not tmp.exists():
            raise FactoryError(f"Could not cut the footage: {proc.stderr.strip()[-500:]}", hint="Check the files of the footage library (`python -m gta_factory footage list`).")
        tmp.rename(cached)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists() or out.stat().st_size != cached.stat().st_size:
        shutil.copyfile(cached, out)
    return out


def scene_data(sc: dict[str, Any], placed: dict[str, Any], beats_t: list[dict[str, Any]], by_beat: dict[int, dict[str, Any]], plan: dict[str, Any], segs: list[dict[str, Any]], ed: dict[str, Any], fps: int) -> dict[str, Any]:
    """What the "shot" template needs: beat ranges, cuts, list numbers and the style, in scene frames."""
    t0 = placed["start_time"]
    out_beats = []
    for t in beats_t:
        b = plan["beats"][t["beat"]]
        m = by_beat[t["beat"]]
        out_beats.append({
            "from": round((t["start"] - t0) * fps),
            "to": round((t["end"] - t0) * fps),
            "number": b.get("number"),
            "item": b.get("item"),
            "numberAt": round((t["number_at"] - t0) * fps) if t.get("number_at") is not None else None,
            "missing": not m["segments"],
            "label": b["text"].strip(),
            "role": b["role"],
        })
    cuts = sorted({round(sg["t"] * fps) for sg in segs})
    return {
        "beats": out_beats,
        "cuts": cuts,
        "style": {
            "push": float(ed["zoom"]["push_per_s"]),
            "punch": float(ed["zoom"]["punch_on_number"]),
            "punchFrames": int(ed["zoom"]["punch_frames"]),
            "saturation": float(ed["grade"]["saturation"]),
            "contrast": float(ed["grade"]["contrast"]),
            "brightness": float(ed["grade"]["brightness"]),
            "badge": bool(ed["number_badge"]),
            "dim": float(ed["footage_dim"]),
            "flash": ed.get("item_transition") == "flash",
            "zoomIn": ed.get("item_transition") == "zoom",
            "alternate": bool(ed["zoom"].get("alternate", True)),
            "shake": float(ed["zoom"].get("shake_on_number", 0)),
        },
    }


def sfx_events(plan: dict[str, Any], beats_t: list[dict[str, Any]], ed: dict[str, Any]) -> list[dict[str, Any]]:
    """Sound design from the beats: a hit on each list number, a whoosh into each item, a riser into the first one."""
    sfx = ed["sfx"]
    out: list[dict[str, Any]] = []
    first_item = next((t for t in beats_t if plan["beats"][t["beat"]].get("number")), None)
    if sfx.get("hook", "none") != "none" and first_item and first_item["start"] > 1.0:
        out.append({"sfx": sfx["hook"], "at": first_item["start"], "timing": "end_at"})
    prev_item = None
    for k, t in enumerate(beats_t):
        b = plan["beats"][t["beat"]]
        if b.get("number"):
            if sfx.get("item_cut", "none") != "none" and k > 0:
                out.append({"sfx": sfx["item_cut"], "at": round(t["start"] - 0.25, 3)})
            if sfx.get("number", "none") != "none":
                out.append({"sfx": sfx["number"], "at": t["number_at"] if t.get("number_at") is not None else t["start"]})
        elif b["role"] == "cta" and sfx.get("cta", "none") != "none":
            out.append({"sfx": sfx["cta"], "at": round(t["speech_start"], 3)})
        elif k > 0 and b.get("item") and b.get("item") == prev_item and sfx.get("inner_cut", "none") != "none":
            out.append({"sfx": sfx["inner_cut"], "at": round(t["start"] - 0.25, 3)})
        prev_item = b.get("item")
    return out


def _tc(seconds: float, fps: int) -> str:
    f = int(round(max(0.0, seconds) * fps))
    return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def edit_decision_list(title: str, fps: int, events: list[dict[str, Any]]) -> str:
    """CMX 3600 EDL of the picture: one event per piece of footage (reel = source file)."""
    lines = [f"TITLE: {title[:60]}", "FCM: NON-DROP FRAME", ""]
    for i, ev in enumerate(events, 1):
        reel = ("BL" if ev["type"] == "blank" else re.sub(r"[^A-Za-z0-9]", "", Path(ev.get("file") or "").stem)[:8] or "AX").upper()
        src_in = ev.get("source_in") or 0.0
        dur = ev["record_out"] - ev["record_in"]
        lines.append(f"{i:03d}  {reel:<8} V     C        {_tc(src_in, fps)} {_tc(src_in + dur, fps)} {_tc(ev['record_in'], fps)} {_tc(ev['record_out'], fps)}")
        if ev.get("file"):
            lines.append(f"* FROM CLIP NAME: {Path(ev['file']).name}")
        lines.append(f"* BEAT {ev['beat'] + 1}: {ev['text'][:70]}")
        if ev.get("hold"):
            lines.append(f"* FREEZE LAST FRAME {ev['hold']:.2f}S")
        lines.append("")
    return "\n".join(lines)


def step_edit(ctx: Any) -> dict[str, Any]:
    from ..branding import stage_brand
    from ..video_editing.render import stage_footage
    from ..video_editing.timeline import build_render_props, scene_local_words

    s = ctx.settings
    f = files(ctx)
    plan = ctx.need(f.plan)
    timing = ctx.need(f.beats)
    matches = ctx.need(f.matches)["matches"]
    scenes = ctx.need(ctx.paths.scenes)
    master = ctx.need(ctx.paths.master)
    script = ctx.need(ctx.paths.script)
    fps = int(master["fps"])
    prof = s.video_profile(ctx.fmt)
    width, height = int(prof["width"]), int(prof["height"])
    ed = editor_params(s)
    profile = load_profile(s)
    write_json(f.style, {"status": profile.get("status"), "reason": profile.get("reason", ""), "reference": profile.get("reference"), "editor": ed})
    placed = {p["scene_number"]: p for p in master["scenes"]}
    by_beat = {m["beat"]: m for m in matches}
    cache_dir = s.jobs_dir / ".cache" / "footage"
    cache_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    for sc in scenes:
        p = placed[sc["scene_number"]]
        bt = [t for t in timing["beats"] if t["scene"] == sc["scene_number"]]
        segs = scene_sequence(sc, p, bt, by_beat)
        clip = cut_sequence(segs, p["duration_frames"], out=ctx.paths.media / f"footage_scene_{sc['scene_number']:03d}.mp4", cache_dir=cache_dir, width=width, height=height, fps=fps)
        credits = sorted({sg["credit"] for t in bt for sg in by_beat[t["beat"]]["segments"] if sg.get("credit")})
        sc["footage"] = {"kind": "video", "clip": str(clip), "credit": " / ".join(credits)}
        sc["data"] = scene_data(sc, p, bt, by_beat, plan, segs, ed, fps)
        for sg in segs:
            m = by_beat[sg["beat"]]
            src = next((x for x in m["segments"] if x["path"] == sg.get("file") and abs(x["start"] - sg.get("start", -1)) < 1e-3), None)
            rec_in = p["start_time"] + sg["t"]
            ev = {"beat": sg["beat"], "text": plan["beats"][sg["beat"]]["text"], "type": sg["type"], "record_in": round(rec_in, 3), "record_out": round(rec_in + sg["d"], 3), "file": (src or {}).get("file"), "source_in": sg.get("start"), "hold": sg.get("hold"), "speed": sg.get("speed"), "status": m["status"]}
            events.append(ev)
            if src:
                manifest.append({
                    "beat": sg["beat"] + 1,
                    "narration": plan["beats"][sg["beat"]]["text"],
                    "narration_start": round(m["start"], 3), "narration_end": round(m["end"], 3),
                    "on_screen_start": ev["record_in"], "on_screen_end": ev["record_out"],
                    "shows": m["concept"], "seen_by_vision_model": m.get("seen") or None,
                    "file": src["file"], "game": src["game"], "source_url": src.get("source_url") or None, "creator": src.get("creator") or None, "credit": src.get("credit"),
                    "source_timestamp": round(src["start"], 3), "clip_start": round(src["start"], 3), "clip_end": round(src["start"] + (sg["d"] - float(sg.get("hold") or 0)) * float(sg.get("speed") or 1.0), 3),
                    "frozen_seconds": sg.get("hold") or 0.0, "playback_speed": sg.get("speed") or 1.0, "source_fps": src.get("fps"),
                    "match_confidence": m["confidence"], "first_frame_confidence": src.get("prob_first"), "verification": m["status"], "rights": src["rights"],
                })
    write_json(ctx.paths.scenes, scenes)
    write_text(f.edl, edit_decision_list(script["title"], fps, events))
    write_json(f.edl_json, {"fps": fps, "events": events})
    write_json(f.manifest, {"video": script["title"], "fps": fps, "segments": manifest, "missing_beats": [{"beat": m["beat"] + 1, "narration": m["text"], "reason": m.get("reason", "")} for m in matches if not m["segments"]]})
    write_json(f.sfx, sfx_events(plan, timing["beats"], ed))
    # render props: the shared builder, then the Short's caption style
    words = read_json(ctx.paths.words, {}) or {}
    alignments = {int(k): v for k, v in (words.get("alignments") or {}).items()}
    footage = stage_footage(s, ctx.job_id, scenes)
    props = build_render_props(s, job_id=ctx.job_id, fmt=ctx.fmt, title=script["title"], today=ctx.today, scenes=scenes, master=master, alignments=alignments, footage=footage, max_words=int(ed["captions"]["max_words"]))
    props["captionStyle"] = {"top": float(ed["captions"]["top"]), "size": float(ed["captions"]["size"]), "uppercase": bool(ed["captions"]["uppercase"])}
    props["brand"] = stage_brand(s)
    write_json(ctx.paths.props, props)
    missing = sum(1 for m in matches if not m["segments"])
    ctx.log(f"edit: {len(events)} cuts over {master['total_seconds']:.1f}s, {missing} beat(s) without footage, style {profile.get('status')}")
    return {"cuts": len(events), "missing_beats": missing, "style": profile.get("status")}


# =========================================================================== cover, metadata
def step_cover(ctx: Any) -> dict[str, Any]:
    """The cover: the best verified frame of the list's top item (or of the hook) under the title."""
    from ..branding import stage_brand
    from ..video_editing.render import bundle_dir, render_stills

    f = files(ctx)
    script = ctx.need(ctx.paths.script)
    plan = ctx.need(f.plan)
    matches = ctx.need(f.matches)["matches"]
    ctx.paths.thumbnails.mkdir(parents=True, exist_ok=True)
    good = [m for m in matches if m["segments"] and m["status"] in ("verified_vision", "verified_clip")] or [m for m in matches if m["segments"]]
    numbered = [m for m in good if plan["beats"][m["beat"]].get("number")]
    pick = min(numbered, key=lambda m: plan["beats"][m["beat"]]["number"]) if numbered else (max(good, key=lambda m: m["confidence"]) if good else None)
    photo = None
    if pick:
        sg = pick["segments"][0]
        photo = ctx.paths.thumbnails / "cover_frame.jpg"
        t = (sg["start"] + sg["end"]) / 2
        from ..footage.library import crop_filter

        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", sg["path"], "-frames:v", "1", "-vf", f"{crop_filter(sg.get('crop'))}scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "2", str(photo)], capture_output=True)
        if not photo.exists():
            photo = None
    nums = [b["number"] for b in plan["beats"] if b.get("number")]
    words = script["title"].upper().split()
    lines = [" ".join(words[i : i + 3]) for i in range(0, min(len(words), 9), 3)] or ["GTA 6"]
    props: dict[str, Any] = {
        "concept": "number" if nums else "stamp", "lines": lines[:3], "accent": lines[-1].split()[-1] if lines else "", "status": None, "motif": "controller",
        "bigText": str(max(nums)) if nums else "", "label": "", "brand": ctx.settings.get("channel.name", ""), "logo": stage_brand(ctx.settings)["logo"] or None,
        "color": "coral", "calendar": None, "photo": None,
    }
    if photo:
        public = bundle_dir(ctx.settings) / "public" / "job_media" / ctx.job_id
        public.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(photo, public / "cover_frame.jpg")
        props["photo"] = f"job_media/{ctx.job_id}/cover_frame.jpg"
    out = ctx.paths.thumbnails / "thumb_1_cover.png"
    render_stills(ctx.settings, [{"composition": "Thumbnail", "props": props, "out": out}], work_dir=ctx.paths.thumbnails, log=ctx.log)
    if out.exists():
        shutil.copyfile(out, f.cover)
    write_json(ctx.paths.thumbnails / "concepts.json", [{"props": props, "file": out.name, "frame_from": pick and {"beat": pick["beat"] + 1, "file": pick["segments"][0]["file"]}}])
    return {"cover": out.name if out.exists() else None, "frame": pick["segments"][0]["file"] if pick else None}


def step_short_metadata(ctx: Any) -> dict[str, Any]:
    from ..youtube_metadata.metadata import build_metadata

    f = files(ctx)
    script = ctx.need(ctx.paths.script)
    master = ctx.need(ctx.paths.master)
    claims = read_json(f.claims, {}) or {}
    # the description lists only the claims a source supports, with that source
    fact_check = [{**c, "original_publisher": c["corroborating_sources"][0]["publisher"]} for c in claims.get("claims") or [] if c.get("status") == "supported" and c.get("corroborating_sources")]
    meta = build_metadata(
        _llm(ctx), ctx.settings, fmt=ctx.fmt, script=script, story={"working_title": script["title"], "angle": script.get("angle") or {}},
        fact_check=fact_check, chapters=[], total_seconds=master["total_seconds"], sources=claims.get("sources") or [], rights=ctx.need(ctx.paths.rights),
        voice_provider=ctx.tts_name, log=ctx.log,
        how="Every clip is real GTA VI footage matched to the words it plays under; sources of the footage are credited below.",
    )
    meta["hashtags"] = list(dict.fromkeys([*(meta.get("hashtags") or []), "Shorts"]))[:4]
    if "#Shorts" not in meta["description"]:
        meta["description"] = meta["description"].rstrip() + "\n\n#Shorts"
    write_json(ctx.paths.metadata, meta)
    write_text(ctx.paths.description, meta["description"] + "\n")
    ctx.db.update_job(ctx.job_id, title=meta["title"])
    return {"title": meta["title"], "tags": len(meta["tags"]), "hashtags": meta["hashtags"]}


# =========================================================================== qc, package
def step_short_qc(ctx: Any) -> dict[str, Any]:
    from ..quality_control.checks import run_qc
    from .qc import production_report, script_checks

    s = ctx.settings
    f = files(ctx)
    master = ctx.need(ctx.paths.master)
    props = ctx.need(ctx.paths.props)
    report = run_qc(
        s, fmt=ctx.fmt, video_path=ctx.paths.final_video, master=master, props=props, cues=ctx.need(ctx.paths.cues), script=ctx.need(ctx.paths.script),
        rights=ctx.need(ctx.paths.rights), mix_stats=read_json(ctx.paths.mix_stats, {}) or {}, thumbnails=sorted(ctx.paths.thumbnails.glob("thumb_*.png")),
        metadata=ctx.need(ctx.paths.metadata), expected_fps=int(master["fps"]),
    )
    extra = script_checks(s, job_root=ctx.paths.root, files=f, video=ctx.paths.final_video, master=master, log=ctx.log)
    report["checks"] += extra["checks"]
    crit = [c for c in report["checks"] if not c["passed"] and c["severity"] == "critical"]
    warn = [c for c in report["checks"] if not c["passed"] and c["severity"] == "warning"]
    report["verdict"] = "fail" if crit else "pass_with_warnings" if warn else "pass"
    report["fully_verified"] = extra["fully_verified"]
    write_json(ctx.paths.qc, report)
    write_json(f.sync_report, extra["sync"])
    write_text(f.report, production_report(ctx, report, extra))
    for c in crit + warn:
        (ctx.warn if c["severity"] == "warning" else ctx.logger.error)(f"QC {c['severity']}: {c['id']} - {c['detail']}")
    ctx.db.merge_summary(ctx.job_id, qc_verdict=report["verdict"], fully_verified=extra["fully_verified"])
    ctx.log(f"QC verdict: {report['verdict'].upper()} - fully verified: {'YES' if extra['fully_verified'] else 'NO'}")
    return {"verdict": report["verdict"], "fully_verified": extra["fully_verified"], "failed": [c["id"] for c in crit + warn]}


def step_short_package(ctx: Any) -> dict[str, Any]:
    s = ctx.settings
    f = files(ctx)
    out = s.outputs_dir / ctx.job_id
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    meta = ctx.need(ctx.paths.metadata)
    master = ctx.need(ctx.paths.master)
    video_name = (slugify(meta["title"])[:60] or "short") + ".mp4"
    shutil.copyfile(ctx.paths.final_video, out / video_name)
    copies = {
        ctx.paths.voiceover: "narration.wav",
        ctx.paths.srt: "captions.srt",
        ctx.paths.vtt: "captions.vtt",
        f.edl: "edit_decision_list.edl",
        f.edl_json: "edit_decision_list.json",
        f.manifest: "footage_manifest.json",
        f.cover: "cover.png",
        ctx.paths.metadata: "metadata.json",
        ctx.paths.description: "description.txt",
        f.source: "script.txt",
        ctx.paths.script_md: "edit_plan.md",
        f.plan: "edit_plan.json",
        f.beats: "beat_timings.json",
        f.matches: "footage_matches.json",
        ctx.paths.fact_check_report: "claims_report.md",
        ctx.paths.rights: "rights_review.json",
        ctx.paths.qc: "qc_report.json",
        f.sync_report: "sync_report.json",
        f.report: "production_report.md",
        f.style: "style_used.json",
    }
    for src, name in copies.items():
        if src.exists():
            shutil.copyfile(src, out / name)
    write_text(out / "title.txt", meta["title"] + "\n")
    write_text(out / "hashtags.txt", " ".join("#" + h for h in meta.get("hashtags") or []) + "\n")
    ctx.db.merge_summary(ctx.job_id, duration=master["total_seconds"], video=str(out / video_name), package=str(out))
    ctx.log(f"package ready: {out}")
    return {"package": str(out), "video": video_name, "files": len(list(out.iterdir()))}


STEP_FUNCS = {
    "script_plan": step_script_plan,
    "claims": step_claims,
    "sync": step_sync,
    "footage": step_footage,
    "match": step_match,
    "edit": step_edit,
    "cover": step_cover,
    "short_metadata": step_short_metadata,
    "short_qc": step_short_qc,
    "short_package": step_short_package,
}
