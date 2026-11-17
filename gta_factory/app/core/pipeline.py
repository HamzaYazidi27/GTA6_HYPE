"""The production pipeline: one job = one video, sixteen resumable steps.

Every step reads its inputs from the job folder, writes its outputs there, and
records a short summary in the database. A failed or edited job is resumed from
any step (``resume --from <step>``); earlier results are reused as they are.

    jobs/<job_id>/
      job.json                  inputs and options
      pipeline.log              full log (credentials scrubbed)
      sources/                  articles.json, corroboration.json
      research/                 analysis.json, verification.json, fact_check.json, fact_check_report.md
      story/story.json          novelty score, decision, angle
      script/                   script.json, script.md
      scenes/scene_breakdown.json
      assets/                   rights_review.json, media/ (licensed b-roll)
      audio/                    scene_###.wav, voice_manifest.json, final_voiceover.wav, final_mix.wav
      subtitles/                words.json, captions.srt, captions.vtt
      render/                   master_timeline.json, props.json, video_silent.mp4
      final/video.mp4
      thumbnails/               thumb_1.png ..., concepts.json
      metadata/                 metadata.json, description.txt
      qc/qc_report.json
    outputs/<job_id>/           the review package (copied from the job folder)
"""
from __future__ import annotations

import os
import re
import shutil
import time
import traceback
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Any, Callable

import yaml

from ..database.db import Database, get_db
from .errors import ConfigError, ExtractionError, FactoryError, NoveltyStop
from .logging import job_logger
from .utils import new_job_id, now_iso, read_json, slugify, write_json, write_text

STEPS: list[tuple[str, str]] = [
    ("extract", "Fetch the articles"),
    ("analyze", "Read the sources"),
    ("corroborate", "Find corroborating coverage"),
    ("fact_check", "Fact-check every claim"),
    ("select", "Novelty score and angle"),
    ("script", "Write and audit the script"),
    ("scenes", "Storyboard"),
    ("rights", "Rights review"),
    ("voice", "Voiceover"),
    ("timeline", "Timing and subtitles"),
    ("render", "Render motion graphics"),
    ("mix", "Mix and encode"),
    ("thumbnail", "Thumbnails"),
    ("metadata", "YouTube metadata"),
    ("qc", "Quality control"),
    ("package", "Package for review"),
]
STEP_NAMES = [s for s, _ in STEPS]

# "Details you missed" videos start from the footage library instead of articles
DETAIL_STEPS: list[tuple[str, str]] = [
    ("details", "Find the details in the footage"),
    ("details_script", "Write the narration"),
    ("details_scenes", "Storyboard"),
]
DETAIL_STEP_NAMES = [s for s, _ in DETAIL_STEPS] + STEP_NAMES[STEP_NAMES.index("rights") :]

# Comparisons and top lists start from several sources and an outline of items (modules/topic)
TOPIC_STEPS: list[tuple[str, str]] = [
    ("research", "Gather the sources"),
    ("outline", "Choose and order the items"),
]
TOPIC_STEP_NAMES = ["research", "analyze", "fact_check", "outline", "script", "scenes"] + STEP_NAMES[STEP_NAMES.index("rights") :]
STEP_TITLES = dict(STEPS + DETAIL_STEPS + TOPIC_STEPS)
ALL_STEP_NAMES = list(STEP_TITLES)


# =========================================================================== job folder
class JobPaths:
    def __init__(self, root: Path):
        self.root = root

    job_json = property(lambda self: self.root / "job.json")
    log = property(lambda self: self.root / "pipeline.log")
    lock = property(lambda self: self.root / ".lock")
    articles = property(lambda self: self.root / "sources" / "articles.json")
    corroboration = property(lambda self: self.root / "sources" / "corroboration.json")
    analysis = property(lambda self: self.root / "research" / "analysis.json")
    verification = property(lambda self: self.root / "research" / "verification.json")
    fact_check = property(lambda self: self.root / "research" / "fact_check.json")
    fact_check_report = property(lambda self: self.root / "research" / "fact_check_report.md")
    story = property(lambda self: self.root / "story" / "story.json")
    script = property(lambda self: self.root / "script" / "script.json")
    script_md = property(lambda self: self.root / "script" / "script.md")
    details = property(lambda self: self.root / "research" / "details.json")
    game_facts = property(lambda self: self.root / "research" / "game_facts.json")
    outline = property(lambda self: self.root / "story" / "outline.json")
    scenes = property(lambda self: self.root / "scenes" / "scene_breakdown.json")
    rights = property(lambda self: self.root / "assets" / "rights_review.json")
    media = property(lambda self: self.root / "assets" / "media")
    audio = property(lambda self: self.root / "audio")
    voice_manifest = property(lambda self: self.root / "audio" / "voice_manifest.json")
    voiceover = property(lambda self: self.root / "audio" / "final_voiceover.wav")
    mix = property(lambda self: self.root / "audio" / "final_mix.wav")
    mix_stats = property(lambda self: self.root / "audio" / "mix_stats.json")
    words = property(lambda self: self.root / "subtitles" / "words.json")
    srt = property(lambda self: self.root / "subtitles" / "captions.srt")
    vtt = property(lambda self: self.root / "subtitles" / "captions.vtt")
    cues = property(lambda self: self.root / "subtitles" / "cues.json")
    render = property(lambda self: self.root / "render")
    master = property(lambda self: self.root / "render" / "master_timeline.json")
    props = property(lambda self: self.root / "render" / "props.json")
    video_silent = property(lambda self: self.root / "render" / "video_silent.mp4")
    final_video = property(lambda self: self.root / "final" / "video.mp4")
    thumbnails = property(lambda self: self.root / "thumbnails")
    metadata = property(lambda self: self.root / "metadata" / "metadata.json")
    description = property(lambda self: self.root / "metadata" / "description.txt")
    qc = property(lambda self: self.root / "qc" / "qc_report.json")
    llm_usage = property(lambda self: self.root / "llm_usage.json")


# =========================================================================== knowledge timeline
def knowledge_files(settings: Any) -> tuple[Path, Path]:
    return settings.root / "knowledge" / "timeline.yaml", settings.root / "knowledge" / "auto_timeline.json"


def load_timeline(settings: Any) -> list[dict[str, Any]]:
    """Verified background events: the operator's file plus CONFIRMED dated claims of earlier videos."""
    manual, auto = knowledge_files(settings)
    events: list[dict[str, Any]] = []
    if manual.exists():
        events += (yaml.safe_load(manual.read_text(encoding="utf-8")) or {}).get("events") or []
    events += read_json(auto, []) or []
    seen, out = set(), []
    for e in sorted((e for e in events if isinstance(e, dict) and e.get("date") and e.get("event")), key=lambda e: str(e["date"])):
        key = (str(e["date"]), str(e["event"]).lower())
        if key not in seen:
            seen.add(key)
            out.append({"date": str(e["date"]), "event": str(e["event"]), "source": str(e.get("source") or "")})
    return out


def remember_confirmed_dates(settings: Any, fact_check: list[dict[str, Any]], job_id: str) -> int:
    _, auto = knowledge_files(settings)
    events = read_json(auto, []) or []
    known = {(e["date"], e["event"].lower()) for e in events}
    added = 0
    for c in fact_check:
        if c["classification"] == "confirmed" and c.get("confidence") == "high" and c.get("event_date"):
            key = (str(c["event_date"])[:10], c["claim"].lower())
            if key not in known:
                events.append({"date": key[0], "event": c["claim"], "source": c["original_publisher"], "job_id": job_id})
                known.add(key)
                added += 1
    if added:
        write_json(auto, events)
    return added


# =========================================================================== context
class JobContext:
    def __init__(self, settings: Any, db: Database, job_id: str):
        self.settings = settings
        self.db = db
        self.job_id = job_id
        self.job = db.get_job(job_id) or {}
        self.paths = JobPaths(settings.jobs_dir / job_id)
        self.options: dict[str, Any] = self.job.get("options") or {}
        self.fmt: str = self.job.get("format") or "long"
        self.today: str = self.options.get("today") or date.today().isoformat()
        self.logger = job_logger(job_id, self.paths.log)
        self.step_index = 0
        self.n_steps = len(db.get_steps(job_id)) or len(STEPS)
        self.kind: str = self.options.get("kind") or "news"
        self._last_progress = 0.0
        self._usage_saved = 0

    # ---- logging and progress
    def log(self, msg: str) -> None:
        self.logger.info(msg)

    def warn(self, msg: str) -> None:
        self.logger.warning(msg)

    def progress(self, fraction: float, msg: str = "") -> None:
        now = time.time()
        if now - self._last_progress < 1.0 and fraction < 1:
            return
        self._last_progress = now
        total = (self.step_index + max(0.0, min(1.0, fraction))) / self.n_steps
        self.db.update_job(self.job_id, progress=round(total, 4))
        if msg:
            self.logger.info(f"  {msg}")

    # ---- providers (created on first use)
    @cached_property
    def llm(self):
        from ...providers.llm import get_llm

        provider = get_llm(self.settings, self.options.get("llm"))
        provider.check()
        self.log(f"LLM provider: {provider.describe()}")
        return provider

    @cached_property
    def tts_name(self) -> str:
        return self.options.get("tts") or self.settings.get("providers.tts.default", "kokoro")

    @cached_property
    def tts(self):
        from ...providers.tts import get_tts

        return get_tts(self.settings, self.tts_name, self.options.get("voice"))

    def save_llm_usage(self) -> None:
        """Append this run's model calls to ``llm_usage.json`` (kept across resumes)."""
        if "llm" not in self.__dict__:
            return
        new = self.llm.usage.log[self._usage_saved :]
        self._usage_saved = len(self.llm.usage.log)
        prev = read_json(self.paths.llm_usage, {}) or {}
        calls = (prev.get("calls") or []) + new
        write_json(self.paths.llm_usage, {"provider": self.llm.describe(), "calls": calls, "total_seconds": round(sum(c["seconds"] for c in calls), 1)})

    # ---- artefacts
    def articles(self) -> list[Any]:
        from ...modules.article_extraction.extractor import Article

        return [Article(**a) for a in read_json(self.paths.articles, [])]

    def corroborating(self) -> list[Any]:
        from ...modules.article_extraction.extractor import Article

        data = read_json(self.paths.corroboration, {}) or {}
        return [Article(**a) for a in data.get("articles", [])]

    # ---- comparisons and top lists
    @property
    def other_game(self) -> dict[str, Any] | None:
        """The game a comparison puts next to GTA 6 (None for a top list)."""
        from ...modules.footage.games import game

        vs = self.options.get("vs")
        return game(self.settings, vs) if vs else None

    def topic_facts(self) -> list[dict[str, Any]]:
        """The fact-check plus the other game's facts (as established claims): what the script may use."""
        from ...modules.topic.writer import game_claims

        facts = read_json(self.paths.fact_check, []) or []
        other = self.other_game
        if other and self.paths.game_facts.exists():
            facts = facts + game_claims(read_json(self.paths.game_facts, []) or [], other)
        return facts

    def need(self, path: Path) -> Any:
        data = read_json(path)
        if data is None:
            raise FactoryError(f"Missing {path.relative_to(self.paths.root)} - an earlier step has not produced it.", hint="Resume the job from an earlier step.")
        return data


# =========================================================================== steps
def step_extract(ctx: JobContext) -> dict[str, Any]:
    from ...modules.article_extraction.extractor import article_from_text, extract_article

    s = ctx.settings
    urls: list[str] = ctx.job.get("urls") or []
    text_files: dict[str, str] = ctx.options.get("text_files") or {}
    articles, failures, seen_before = [], [], []
    for url in urls:
        try:
            if url in text_files:
                p = Path(text_files[url]).expanduser()
                if not p.exists():
                    raise ExtractionError(f"Text file for {url} not found: {p}")
                art = article_from_text(url, p.read_text(encoding="utf-8"), tier_of=s.source_tier)
            else:
                art = extract_article(url, tier_of=s.source_tier)
        except ExtractionError as exc:
            failures.append({"url": url, "error": exc.message, "hint": exc.hint})
            ctx.warn(f"could not read {url}: {exc.message}")
            continue
        for w in art.warnings:
            ctx.warn(f"{art.publisher}: {w}")
        prev = ctx.db.remember_article(url=art.url, job_id=ctx.job_id, publisher=art.publisher, title=art.title, published_at=art.published_at, content_hash=art.content_hash)
        if prev:
            seen_before.append({"url": art.url, "job_id": prev.get("job_id"), "first_seen_at": prev.get("first_seen_at")})
            ctx.warn(f"{art.url} was already used by job {prev.get('job_id')} (story memory will check for duplicates)")
        articles.append(art)
        ctx.log(f"article: {art.publisher} | {art.title[:90]} | {art.word_count} words | {art.extraction_method}")
    if not articles:
        first = failures[0] if failures else {"error": "no URL given", "hint": ""}
        raise ExtractionError(f"None of the {len(urls)} article(s) could be read. First error: {first['error']}", hint=first.get("hint") or "Pass the article text with --text-file URL=path.")
    write_json(ctx.paths.articles, [a.to_dict() for a in articles])
    ctx.db.update_job(ctx.job_id, title=articles[0].title[:140])
    return {"articles": len(articles), "words": sum(a.word_count for a in articles), "failed": failures, "seen_before": seen_before}


def step_research(ctx: JobContext) -> dict[str, Any]:
    """Topic videos: the operator's sources, plus the best articles the news search finds."""
    from ...modules.topic.research import find_sources

    o = ctx.options
    urls = [u for u in ctx.job.get("urls") or [] if re.match(r"^https?://", u)]
    res = find_sources(ctx.settings, topic=o["topic"], style=o["style"], urls=urls, text_files=o.get("text_files"), other=ctx.other_game, search=not o.get("no_search"), log=ctx.log)
    if not res["articles"]:
        first = res["failures"][0] if res["failures"] else None
        raise ExtractionError(
            "No source could be read for this topic." + (f" First error: {first['error']}" if first else ""),
            hint="Give sources with --source URL (previews, interviews, the Rockstar Newswire), or --text-file URL=PATH for a page that cannot be fetched.",
        )
    write_json(ctx.paths.articles, [a.to_dict() for a in res["articles"]])
    write_json(ctx.paths.corroboration, {"queries": res["queries"], "providers": [], "candidates_found": res["found_by_search"], "articles": [], "extraction_failures": res["failures"], "coverage_signals": [], "topic": True})
    ctx.db.update_job(ctx.job_id, title=o["topic"][:140])
    return {"articles": len(res["articles"]), "found_by_search": res["found_by_search"], "failed": len(res["failures"]), "words": sum(a.word_count for a in res["articles"])}


def topic_count(ctx: JobContext) -> int:
    vertical = ctx.settings.format(ctx.fmt).get("aspect") == "9:16"
    return int(ctx.options.get("count") or ctx.settings.get("topic.count_short" if vertical else "topic.count_long", 4 if vertical else 10))


def step_analyze(ctx: JobContext) -> dict[str, Any]:
    from ...modules.news_research.research import analyze_sources

    if ctx.kind == "topic":
        from ...modules.topic.research import analyze_topic

        analysis = analyze_topic(ctx.llm, ctx.articles(), topic=ctx.options["topic"], style=ctx.options["style"], other=ctx.other_game, count=topic_count(ctx), today=ctx.today)
        write_json(ctx.paths.analysis, analysis)
        unverified = sum(1 for c in analysis["claims"] if not c["evidence_verified"])
        if unverified:
            ctx.warn(f"{unverified} claim(s) have no verbatim evidence in the source text")
        return {"claims": len(analysis["claims"]), "quotes": len(analysis.get("quotes") or []), "unverified_evidence": unverified}

    analysis = analyze_sources(ctx.llm, ctx.articles(), ctx.today)
    write_json(ctx.paths.analysis, analysis)
    ctx.db.update_job(ctx.job_id, title=analysis.get("story_headline", "")[:140])
    unverified = sum(1 for c in analysis["claims"] if not c["evidence_verified"])
    if unverified:
        ctx.warn(f"{unverified} claim(s) have no verbatim evidence in the source text")
    return {"headline": analysis.get("story_headline"), "claims": len(analysis["claims"]), "quotes": len(analysis.get("quotes") or []), "unverified_evidence": unverified}


def step_corroborate(ctx: JobContext) -> dict[str, Any]:
    from ...modules.news_research.research import gather_corroboration

    if ctx.options.get("no_search"):
        write_json(ctx.paths.corroboration, {"queries": [], "providers": [], "candidates_found": 0, "articles": [], "extraction_failures": [], "coverage_signals": [], "skipped": True})
        ctx.warn("corroboration search disabled for this job (--no-search): claims can only be REPORTED, never CONFIRMED by a second source")
        return {"skipped": True}
    analysis = ctx.need(ctx.paths.analysis)
    result = gather_corroboration(ctx.settings, analysis, ctx.articles(), log=ctx.log)
    result["articles"] = [a.to_dict() for a in result["articles"]]
    write_json(ctx.paths.corroboration, result)
    return {"queries": len(result["queries"]), "candidates": result["candidates_found"], "articles": len(result["articles"]), "publishers_covering": len(result["coverage_signals"])}


def step_fact_check(ctx: JobContext) -> dict[str, Any]:
    from ...modules.fact_checking.verify import build_fact_check, render_fact_check_report, verify_claims

    analysis = ctx.need(ctx.paths.analysis)
    corroborating = ctx.corroborating()
    if ctx.kind == "topic":  # several sources on one topic: each one corroborates the others, never itself
        from ...modules.topic.research import drop_self_support

        articles = ctx.articles()
        verification = verify_claims(ctx.llm, analysis, articles if len(articles) > 1 else [], load_timeline(ctx.settings), ctx.today)
        verification = drop_self_support(verification, analysis["claims"])
    else:
        verification = verify_claims(ctx.llm, analysis, corroborating, load_timeline(ctx.settings), ctx.today)
    fact_check = build_fact_check(analysis, verification)
    write_json(ctx.paths.verification, verification)
    write_json(ctx.paths.fact_check, fact_check)
    write_text(ctx.paths.fact_check_report, render_fact_check_report(analysis["story_headline"], fact_check, verification, ctx.articles(), corroborating, ctx.today))
    counts = {k: sum(1 for c in fact_check if c["classification"] == k) for k in ("confirmed", "reported", "rumor", "speculation")}
    ctx.log("fact-check: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return {**counts, "contradictions": len(verification.get("contradictions") or []), "context_facts": len(verification.get("additional_facts") or [])}


def step_select(ctx: JobContext) -> dict[str, Any]:
    from ...modules.story_selection.novelty import select_story

    story = select_story(
        ctx.llm,
        ctx.settings,
        analysis=ctx.need(ctx.paths.analysis),
        fact_check=ctx.need(ctx.paths.fact_check),
        verification=ctx.need(ctx.paths.verification),
        articles=[a.to_dict() for a in ctx.articles()],
        stories=ctx.db.list_stories(exclude_job=ctx.job_id),
        fmt=ctx.fmt,
        today=ctx.today,
    )
    write_json(ctx.paths.story, story)
    ctx.db.merge_summary(ctx.job_id, novelty_score=story["novelty_score"], decision=story["decision"], angle=story["angle"].get("thesis"))
    ctx.log(f"NEWS NOVELTY SCORE {story['novelty_score']}/100 -> {story['decision']}: {story['decision_reason']}")
    if story.get("recommended_format") and story["recommended_format"] != ctx.fmt:
        ctx.log(f"the editor would recommend the '{story['recommended_format']}' format for this story (job uses '{ctx.fmt}')")
    detail = {"novelty_score": story["novelty_score"], "decision": story["decision"], "reason": story["decision_reason"], "angle": story["angle"].get("title")}
    if story["decision"].startswith("skip") and not ctx.options.get("force"):
        raise NoveltyStop(story["decision_reason"], hint="Nothing was produced. Use --force to make the video anyway, or submit a source with genuinely new information.", step="select")
    return detail


def step_outline(ctx: JobContext) -> dict[str, Any]:
    """Topic videos: which items, in which order, each resting on fact-checked claims (and the other game's facts)."""
    from ...modules.footage.games import load_facts
    from ...modules.topic.outline import plan_items

    other = ctx.other_game
    game_facts = load_facts(ctx.settings, other["id"]) if other else []
    write_json(ctx.paths.game_facts, game_facts)
    outline = plan_items(ctx.llm, topic=ctx.options["topic"], style=ctx.options["style"], count=topic_count(ctx), fact_check=ctx.need(ctx.paths.fact_check), game_facts=game_facts, other=other, today=ctx.today, log=ctx.log)
    write_json(ctx.paths.outline, outline)
    ctx.db.update_job(ctx.job_id, title=outline["working_title"][:140])
    ctx.db.merge_summary(ctx.job_id, items=len(outline["items"]))
    for it in outline["items"]:
        ctx.log(f"outline: #{it['n']} {it['title']} [{it['status']}] " + ", ".join(it["claim_ids"] + it.get("game_fact_ids", [])))
    return {"items": len(outline["items"]), "titles": " | ".join(it["title"] for it in outline["items"])[:300]}


def step_script(ctx: JobContext) -> dict[str, Any]:
    from ...modules.script_generation.writer import render_script_markdown, write_script

    if ctx.kind == "topic":
        from ...modules.topic.writer import script_markdown, write_topic_script

        outline = ctx.need(ctx.paths.outline)
        script = write_topic_script(
            ctx.llm,
            ctx.settings,
            fmt=ctx.fmt,
            outline=outline,
            fact_check=ctx.need(ctx.paths.fact_check),
            game_facts=read_json(ctx.paths.game_facts, []) or [],
            other=ctx.other_game,
            quotes=(ctx.need(ctx.paths.analysis).get("quotes") or []),
            timeline=load_timeline(ctx.settings),
            today=ctx.today,
            target=float(ctx.options["target_seconds"]) if ctx.options.get("target_seconds") else None,
            audit=ctx.options.get("audit", True),
            log=ctx.log,
        )
        write_json(ctx.paths.script, script)
        write_text(ctx.paths.script_md, script_markdown(script, ctx.topic_facts()))
        ctx.db.update_job(ctx.job_id, title=script["title"][:140])
        q = script["quality"]
        ctx.log(f"script: '{script['title']}' - {script['word_budget']['actual']} words (~{script['estimated_seconds']:.0f}s), {len(script['items'])} items, audit {q['audit_verdict']}")
        return {"title": script["title"], "words": script["word_budget"]["actual"], "estimated_seconds": script["estimated_seconds"], "audit": q["audit_verdict"], "remaining_issues": len(q["remaining_lint_issues"]) + len(q["remaining_audit_issues"])}

    story = ctx.need(ctx.paths.story)
    fact_check = ctx.need(ctx.paths.fact_check)
    target = float(ctx.options.get("target_seconds") or ctx.settings.format(ctx.fmt)["target_seconds"])
    script = write_script(
        ctx.llm,
        ctx.settings,
        fmt=ctx.fmt,
        target_seconds=target,
        analysis=ctx.need(ctx.paths.analysis),
        fact_check=fact_check,
        verification=ctx.need(ctx.paths.verification),
        story=story,
        timeline=load_timeline(ctx.settings),
        today=ctx.today,
        audit=ctx.options.get("audit", True),
        log=ctx.log,
    )
    write_json(ctx.paths.script, script)
    write_text(ctx.paths.script_md, render_script_markdown(script, story, fact_check))
    q = script["quality"]
    ctx.log(f"script: '{script['title']}' - {script['word_budget']['actual']} words (~{script['estimated_seconds']:.0f}s), audit {q['audit_verdict']}")
    return {"title": script["title"], "words": script["word_budget"]["actual"], "estimated_seconds": script["estimated_seconds"], "audit": q["audit_verdict"], "remaining_issues": len(q["remaining_lint_issues"]) + len(q["remaining_audit_issues"])}


def _broll_possible(settings: Any) -> bool:
    from ...modules.asset_management.rights import load_operator_assets
    from ...providers.stock import get_stock_providers

    return bool(get_stock_providers(settings)) or any(a["sidecar"] for a in load_operator_assets(settings.assets_dir / "licensed_visuals"))


def step_scenes(ctx: JobContext) -> dict[str, Any]:
    from ...modules.script_generation.scenes import plan_scenes

    if ctx.kind == "topic":  # built from the script: item cards, split screens, footage of the game being talked about
        from ...modules.topic.scenes import build_topic_scenes

        scenes = build_topic_scenes(ctx.settings, ctx.need(ctx.paths.script), fmt=ctx.fmt, facts=ctx.topic_facts(), today=ctx.today)
        write_json(ctx.paths.scenes, scenes)
        templates = [s["template"] for s in scenes]
        games = [s.get("footage_game") for s in scenes if s["template"] == "footage"]
        return {"scenes": len(scenes), "templates": {t: templates.count(t) for t in sorted(set(templates))}, "other_game_scenes": sum(1 for g in games if g != "gta6")}

    articles = [a.to_dict() for a in ctx.articles()] + [a.to_dict() for a in ctx.corroborating()]
    allow_broll = bool(ctx.options.get("allow_broll", _broll_possible(ctx.settings)))
    scenes = plan_scenes(
        ctx.llm,
        ctx.settings,
        script=ctx.need(ctx.paths.script),
        fact_check=ctx.need(ctx.paths.fact_check),
        quotes=(ctx.need(ctx.paths.analysis).get("quotes") or []),
        articles=articles,
        fmt=ctx.fmt,
        today=ctx.today,
        allow_broll=allow_broll,
        log=ctx.log,
    )
    write_json(ctx.paths.scenes, scenes)
    templates = [s["template"] for s in scenes]
    return {"scenes": len(scenes), "templates": {t: templates.count(t) for t in sorted(set(templates))}, "broll_allowed": allow_broll}


def step_details(ctx: JobContext) -> dict[str, Any]:
    """Look at the footage with the vision model (cached) and pick the best details for this video."""
    from ...modules.details.find import make_verifier, pick_details, scan

    s = ctx.settings
    cands = scan(s, videos=ctx.options.get("videos") or None, model=ctx.options.get("vision_model"), log=ctx.log, progress=ctx.progress)
    vertical = s.format(ctx.fmt).get("aspect") == "9:16"
    count = int(ctx.options.get("count") or s.get("details.count_short" if vertical else "details.count_long", 5 if vertical else 12))
    picked = pick_details(s, cands, count, verify=make_verifier(s, model=ctx.options.get("vision_model"), log=ctx.log))
    if len(picked) < 2:
        raise FactoryError(
            f"Only {len(picked)} detail(s) found in this footage ({len(cands)} candidate(s) checked).",
            hint="Add more footage (`footage update` / `footage add`), pick another video, or use a bigger vision model (footage.vision_model, e.g. qwen2.5vl:32b).",
        )
    if len(picked) < count:
        ctx.warn(f"details: only {len(picked)} good detail(s) found (asked for {count})")
    write_json(ctx.paths.details, {"details": picked, "candidates": len(cands), "videos": sorted({d["file"] for d in picked})})
    for d in picked:
        ctx.log(f"details: #{d['id'][1:]} {d['short_title']} {d['timecode']} - {d['label']}" + (f" « {d['text']} »" if d.get("text") else ""))
    return {"details": len(picked), "candidates": len(cands)}


def step_details_script(ctx: JobContext) -> dict[str, Any]:
    from ...modules.details.produce import script_markdown, write_script

    details = ctx.need(ctx.paths.details)["details"]
    script = write_script(ctx.llm, ctx.settings, details, fmt=ctx.fmt, log=ctx.log)
    write_json(ctx.paths.script, script)
    write_text(ctx.paths.script_md, script_markdown(script, details))
    ctx.db.update_job(ctx.job_id, title=script["title"])
    return {"title": script["title"], "words": script["word_budget"]["actual"]}


def step_details_scenes(ctx: JobContext) -> dict[str, Any]:
    from ...modules.details.produce import build_scenes

    scenes = build_scenes(ctx.need(ctx.paths.script), ctx.need(ctx.paths.details)["details"])
    write_json(ctx.paths.scenes, scenes)
    return {"scenes": len(scenes), "details": sum(1 for sc in scenes if sc["template"] == "detail")}


def step_rights(ctx: JobContext) -> dict[str, Any]:
    from ...modules.asset_management.audio_library import build_library, library_ready
    from ...modules.asset_management.rights import review_assets
    from ...modules.footage.select import assign_footage

    missing = library_ready(ctx.settings.assets_dir)
    if missing:  # the music beds and sound effects are synthesised locally: make them now (about 20 s, one time)
        ctx.log(f"audio library incomplete ({', '.join(missing[:4])}): generating the original music beds and sound effects (one time) ...")
        build_library(ctx.settings.assets_dir, log=ctx.log)
        missing = library_ready(ctx.settings.assets_dir)
    if missing:
        raise ConfigError(f"The audio library is incomplete ({', '.join(missing[:4])}).", hint="Run `python -m gta_factory setup` once to generate the original music beds and sound effects.")
    scenes = ctx.need(ctx.paths.scenes)
    vertical = ctx.settings.format(ctx.fmt).get("aspect") == "9:16"
    review = review_assets(ctx.settings, scenes, media_dir=ctx.paths.media, tts_provider=ctx.tts_name, vertical=vertical, log=ctx.log)
    footage = assign_footage(ctx.settings, scenes, log=ctx.log)
    if footage["ledger"]:
        review["assets"] = footage["ledger"] + review["assets"]
        review["credits"] = sorted(set(review["credits"]) | {e["attribution"] for e in footage["ledger"] if e.get("attribution")})
        review["decisions"] += [{"scene_number": sc["scene_number"], "outcome": f"{sc[key].get('game', 'gta6')}_{sc[key]['kind']}", "asset_ids": sc[key]["asset_ids"]} for sc in scenes for key in ("footage", "footage_b") if sc.get(key)]
        what = "Real footage" if footage["summary"].get("mode") == "topic" else "Real GTA 6 footage"
        review["summary"] += f" {what} on {footage['summary']['share']:.0%} of the narration ({len(footage['summary'].get('matched') or [])} spoken mention(s) shown)."
    review["footage"] = footage["summary"]
    write_json(ctx.paths.scenes, scenes)  # b-roll scenes may have been resolved or converted, footage assigned
    write_json(ctx.paths.rights, review)
    for a in review["assets"] + review["rejected"]:
        ctx.db.upsert_asset(ctx.job_id, a)
    ctx.log(f"rights: {review['summary']}")
    return {"verdict": review["verdict"], "summary": review["summary"], "footage_share": footage["summary"].get("share", 0.0), "footage_matches": len(footage["summary"].get("matched") or [])}


def step_voice(ctx: JobContext) -> dict[str, Any]:
    from ...modules.voice_generation.engine import generate_voiceover

    if ctx.tts_name == "kokoro":  # the free local voice: download it here if `setup` was skipped (one time, ~350 MB)
        from ...modules.asset_management.models import ensure_kokoro

        ensure_kokoro(ctx.settings, log=ctx.log)
    scenes = ctx.need(ctx.paths.scenes)
    force = set(ctx.options.get("force_voice_scenes") or [])
    voice = generate_voiceover(
        ctx.tts,
        ctx.settings,
        scenes,
        cache_dir=ctx.settings.jobs_dir / ".cache" / "tts",
        audio_dir=ctx.paths.audio,
        force_scenes=force or None,
        progress=lambda done, total, msg: ctx.progress(done / total, f"voice {done}/{total}: {msg}"),
    )
    write_json(ctx.paths.voice_manifest, voice)
    total = sum(m["duration"] for m in voice["scenes"])
    return {"provider": voice["provider"], "voice": voice["voice"], "scenes": len(voice["scenes"]), "reused": voice["reused_from_cache"], "narration_seconds": round(total, 1)}


def step_timeline(ctx: JobContext) -> dict[str, Any]:
    from ...modules.subtitles.align import align_scene, build_captions, to_srt, to_vtt
    from ...modules.footage.select import cut_scene_clips
    from ...modules.video_editing.render import stage_footage, stage_media
    from ...modules.video_editing.timeline import build_render_props, scene_local_words
    from ...modules.voice_generation.engine import build_timeline

    s = ctx.settings
    scenes = ctx.need(ctx.paths.scenes)
    voice = ctx.need(ctx.paths.voice_manifest)
    fps = int(s.video_profile(ctx.fmt)["fps"])
    master = build_timeline(s, scenes, voice, ctx.paths.audio, fps)
    write_json(ctx.paths.master, master)
    by_n = {m["scene_number"]: m for m in voice["scenes"]}
    placed = {p["scene_number"]: p for p in master["scenes"]}
    alignments: dict[int, dict[str, Any]] = {}
    all_words: list[dict[str, Any]] = []
    methods: dict[str, int] = {}
    for i, sc in enumerate(scenes):
        m = by_n[sc["scene_number"]]
        al = align_scene(sc["narration"], m["sentences"], ctx.paths.audio / m["file"], m.get("provider_words") or [], use_whisper=ctx.options.get("whisper", True))
        alignments[sc["scene_number"]] = al
        methods[al["method"]] = methods.get(al["method"], 0) + 1
        base = placed[sc["scene_number"]]["start_time"] + placed[sc["scene_number"]]["voice_offset"]
        all_words += [{"w": w["w"], "s": round(w["s"] + base, 3), "e": round(w["e"] + base, 3), "scene": sc["scene_number"]} for w in al["words"]]
        ctx.progress((i + 1) / len(scenes), "")
    q = s.get("quality_control") or {}
    cues = build_captions(all_words, max_chars=int(q.get("max_caption_chars_per_line", 42)))
    write_json(ctx.paths.words, {"words": all_words, "methods": methods})
    write_json(ctx.paths.cues, cues)
    write_text(ctx.paths.srt, to_srt(cues))
    write_text(ctx.paths.vtt, to_vtt(cues))
    script = ctx.need(ctx.paths.script)
    if any(sc.get("footage") for sc in scenes):
        prof = s.video_profile(ctx.fmt)
        local_words = {n: scene_local_words(al["words"], placed[n]["voice_offset"]) for n, al in alignments.items()}
        cut_scene_clips(s, scenes, {n: p["duration_frames"] / fps for n, p in placed.items()}, out_dir=ctx.paths.media, width=int(prof["width"]), height=int(prof["height"]), fps=fps, words=local_words, log=ctx.log)
        write_json(ctx.paths.scenes, scenes)
    media = stage_media(s, ctx.job_id, scenes)
    footage = stage_footage(s, ctx.job_id, scenes)
    props = build_render_props(s, job_id=ctx.job_id, fmt=ctx.fmt, title=script["title"], today=ctx.today, scenes=scenes, master=master, alignments=alignments, media_urls=media, footage=footage)
    write_json(ctx.paths.props, props)
    ctx.log(f"timeline: {master['total_seconds']:.1f}s, {len(scenes)} scenes, {len(cues)} subtitle cues, alignment {methods}, voice {master['voiceover']['lufs']:.1f} LUFS")
    return {"seconds": master["total_seconds"], "frames": master["total_frames"], "cues": len(cues), "alignment": methods}


def step_render(ctx: JobContext) -> dict[str, Any]:
    from ...modules.video_editing.render import render_video, stage_footage, stage_media

    props = ctx.need(ctx.paths.props)
    stage_media(ctx.settings, ctx.job_id, ctx.need(ctx.paths.scenes))
    stage_footage(ctx.settings, ctx.job_id, ctx.need(ctx.paths.scenes))
    t0 = time.time()
    info = render_video(ctx.settings, props, work_dir=ctx.paths.render, out=ctx.paths.video_silent, log=ctx.log, progress=ctx.progress)
    info["seconds"] = round(time.time() - t0, 1)
    return info


def step_mix(ctx: JobContext) -> dict[str, Any]:
    from ...modules.video_editing.mix import build_mix, mux

    master = ctx.need(ctx.paths.master)
    stats = build_mix(ctx.settings, master=master, scenes=ctx.need(ctx.paths.scenes), voice_path=ctx.paths.voiceover, out_wav=ctx.paths.mix)
    write_json(ctx.paths.mix_stats, stats)
    mux(ctx.settings, video=ctx.paths.video_silent, audio=ctx.paths.mix, out=ctx.paths.final_video)
    ctx.log(f"mix: {stats['output_lufs']:.1f} LUFS, true peak {stats['output_true_peak_db']:.1f} dBTP, voice {stats.get('voice_over_music_db', 0):.1f} dB over music")
    return {k: stats.get(k) for k in ("output_lufs", "output_true_peak_db", "voice_over_music_db", "sfx_events", "music_mood")}


def step_thumbnail(ctx: JobContext) -> dict[str, Any]:
    from ...modules.footage.select import thumbnail_photos
    from ...modules.thumbnail_generation.concepts import design_thumbnails
    from ...modules.video_editing.render import bundle_dir
    from ...modules.video_editing.render import render_stills

    script = ctx.need(ctx.paths.script)
    ctx.paths.thumbnails.mkdir(parents=True, exist_ok=True)
    for old in ctx.paths.thumbnails.glob("thumb_*.png"):
        old.unlink()
    if ctx.kind == "details":  # a close-up of the best details, circled
        from ...modules.details.produce import thumbnail_concepts

        concepts = thumbnail_concepts(ctx.settings, script, ctx.need(ctx.paths.details)["details"], out_dir=ctx.paths.thumbnails / "photos")
        pics = [Path(c["photo_file"]) for c in concepts]
    elif ctx.kind == "topic":  # "GTA 6 vs ..." with a picture of each game, or the number of entries
        from ...modules.topic.produce import thumbnail_concepts as topic_thumbnails

        concepts = topic_thumbnails(ctx.llm, ctx.settings, script=script, fact_check=ctx.need(ctx.paths.fact_check), other=ctx.other_game, out_dir=ctx.paths.thumbnails / "photos", log=ctx.log)
        pics = [Path(c.get("photo_file") or ctx.paths.thumbnails / "photos" / "none") for c in concepts]
    else:
        concepts = design_thumbnails(ctx.llm, ctx.settings, script=script, story=ctx.need(ctx.paths.story), fact_check=ctx.need(ctx.paths.fact_check), log=ctx.log)
        # real GTA 6 pictures behind the thumbnail text (from the footage library), one per concept
        pics = thumbnail_photos(ctx.settings, f"{script.get('title', '')} " + " ".join(" ".join(c["props"]["lines"]) for c in concepts), len(concepts), out_dir=ctx.paths.thumbnails / "photos")
    public = bundle_dir(ctx.settings) / "public" / "job_media" / ctx.job_id
    for i, (c, pic) in enumerate(zip(concepts, pics), 1):
        if not pic.exists():
            continue
        public.mkdir(parents=True, exist_ok=True)
        dest = public / f"thumb_photo_{i}{pic.suffix.lower()}"
        shutil.copyfile(pic, dest)
        c["props"]["photo"] = f"job_media/{ctx.job_id}/{dest.name}"
        c["photo_file"] = str(pic)
        second = Path(c.get("photo2_file") or "")
        if c.get("photo2_file") and second.exists():  # the other game's picture of a "GTA 6 vs ..." thumbnail
            dest2 = public / f"thumb_photo_{i}_b{second.suffix.lower()}"
            shutil.copyfile(second, dest2)
            c["props"]["photo2"] = f"job_media/{ctx.job_id}/{dest2.name}"
    stills = [{"composition": "Thumbnail", "props": c["props"], "out": ctx.paths.thumbnails / f"thumb_{i}_{c['props']['concept']}.png"} for i, c in enumerate(concepts, 1)]
    render_stills(ctx.settings, stills, work_dir=ctx.paths.thumbnails, log=ctx.log)
    for c, st in zip(concepts, stills):
        c["file"] = Path(st["out"]).name
    write_json(ctx.paths.thumbnails / "concepts.json", concepts)
    return {"thumbnails": [c["file"] for c in concepts]}


def _sources(ctx: JobContext) -> list[dict[str, Any]]:
    seen, out = set(), []
    for a in ctx.articles() + ctx.corroborating():
        if a.url not in seen:
            seen.add(a.url)
            out.append({"publisher": a.publisher, "title": a.title, "url": a.url, "tier": a.source_tier, "published_at": a.published_at})
    return out


def step_metadata(ctx: JobContext) -> dict[str, Any]:
    from ...modules.video_editing.timeline import build_chapters
    from ...modules.youtube_metadata.metadata import build_metadata

    master = ctx.need(ctx.paths.master)
    scenes = ctx.need(ctx.paths.scenes)
    placed = {p["scene_number"]: p for p in master["scenes"]}
    script = ctx.need(ctx.paths.script)
    details_extra: dict[str, Any] = {}
    if ctx.kind == "topic":
        from ...modules.topic.produce import description_sections, extra_tags

        sections, how = description_sections(script, ctx.other_game)
        details_extra = {"extra_sections": sections, "how": how, "extra_tags": extra_tags(script, ctx.other_game)}
    if ctx.kind == "details":
        from ...modules.details.produce import description_block

        details_extra = {
            "extra_sections": [("THE DETAILS (and where to find them in the original footage)", description_block(script, ctx.need(ctx.paths.details)["details"]))],
            "how": "Every detail is shown at its timestamp in the official footage, so you can check it yourself. Anything beyond what is visible is marked as a guess.",
        }
    meta = build_metadata(
        ctx.llm,
        ctx.settings,
        fmt=ctx.fmt,
        script=script,
        story=ctx.need(ctx.paths.story) if ctx.kind == "news" else {"working_title": script["title"], "angle": script.get("angle") or {}},
        fact_check=ctx.need(ctx.paths.fact_check) if ctx.kind in ("news", "topic") else [],
        chapters=build_chapters(scenes, placed),
        total_seconds=master["total_seconds"],
        sources=_sources(ctx),
        rights=ctx.need(ctx.paths.rights),
        voice_provider=ctx.tts_name,
        log=ctx.log,
        **details_extra,
    )
    write_json(ctx.paths.metadata, meta)
    write_text(ctx.paths.description, meta["description"] + "\n")
    ctx.db.update_job(ctx.job_id, title=meta["title"])
    return {"title": meta["title"], "chapters": len(meta["chapters"]), "tags": len(meta["tags"])}


def step_qc(ctx: JobContext) -> dict[str, Any]:
    from ...modules.quality_control.checks import run_qc

    report = run_qc(
        ctx.settings,
        fmt=ctx.fmt,
        video_path=ctx.paths.final_video,
        master=ctx.need(ctx.paths.master),
        props=ctx.need(ctx.paths.props),
        cues=ctx.need(ctx.paths.cues),
        script=ctx.need(ctx.paths.script),
        rights=ctx.need(ctx.paths.rights),
        mix_stats=read_json(ctx.paths.mix_stats, {}) or {},
        thumbnails=sorted(ctx.paths.thumbnails.glob("thumb_*.png")),
        metadata=ctx.need(ctx.paths.metadata),
    )
    write_json(ctx.paths.qc, report)
    failed = [c for c in report["checks"] if not c["passed"] and c["severity"] in ("critical", "warning")]
    for c in failed:
        (ctx.warn if c["severity"] == "warning" else ctx.logger.error)(f"QC {c['severity']}: {c['id']} - {c['detail']}")
    ctx.db.merge_summary(ctx.job_id, qc_verdict=report["verdict"])
    ctx.log(f"QC verdict: {report['verdict'].upper()} ({sum(1 for c in report['checks'] if c['passed'])}/{len(report['checks'])} checks passed)")
    return {"verdict": report["verdict"], "failed": [c["id"] for c in failed]}


REVIEW_TEMPLATE = """# Paquet de relecture - {title}

Job `{job_id}` - format **{fmt}** - durée **{duration}** - contrôle qualité **{verdict}**

## Fichiers
- `{video}` : la vidéo finale (H.264 / AAC, {lufs:.1f} LUFS)
- `thumbnails/` : {n_thumbs} miniature(s) - choisis-en une
- `captions.srt` / `captions.vtt` : sous-titres à téléverser sur YouTube
- `description.txt`, `metadata.json` : titre, description, tags, chapitres
- `script.md`, `fact_check_report.md` : le script et la vérification de chaque affirmation
- `rights_review.json`, `qc_report.json`, `sources.json` : droits, contrôle qualité, sources

## Avant de publier
- [ ] Regarder la vidéo en entier (voix, prononciation, sous-titres)
- [ ] Relire `fact_check_report.md` : rien de présenté comme CONFIRMÉ qui ne le soit pas
- [ ] Choisir la miniature et le titre ({n_titles} propositions dans `metadata.json`)
- [ ] Vérifier les points du contrôle qualité ci-dessous

## Publier
`python -m gta_factory publish {job_id}` envoie la vidéo **en privé** avec la miniature, les sous-titres et la playlist
(`--title N`, `--thumbnail N`, `--publish-at 2026-11-20T18:00` pour programmer, `--dry-run` pour voir ce qui serait envoyé).
Vérifie ensuite dans YouTube Studio, passe-la en public, et épingle le commentaire proposé dans `metadata.json`.

## Points du contrôle qualité à regarder
{qc_points}
"""


def _footage_point(ctx: JobContext) -> str:
    fo = (read_json(ctx.paths.rights, {}) or {}).get("footage") or {}
    if not fo.get("share"):
        return ""
    games = fo.get("games") or {}
    others = "".join(f", {g['name']} sur {g['scenes']} scène(s)" for gid, g in games.items() if gid != "gta6")
    split = f", {fo['split_scenes']} écran(s) partagé(s)" if fo.get("split_scenes") else ""
    point = (
        f"\n- [ ] Images réelles{'' if games else ' de GTA 6'} sur {fo['share']:.0%} de la vidéo ({fo.get('video_scenes', 0)} scènes trailer, {fo.get('photo_scenes', 0)} screenshots{split}{others}) :"
        " vérifier qu'aucun plan ne contredit la narration (et que chaque jeu montre bien ses propres images). Une revendication Content ID de Rockstar/Take-Two est possible après l'upload (regarder l'onglet Droits d'auteur dans YouTube Studio)."
    )
    empty = [f"{g['name']} (`python -m gta_factory footage update --game {gid}`)" for gid, g in games.items() if not (g.get("library") or {}).get("usable_shots") and not (g.get("library") or {}).get("photos")]
    if empty:
        point += "\n- [ ] Bibliothèque vide, ses scènes n'ont pas d'images : " + ", ".join(empty) + ", puis « refaire depuis : Droits »."
    matched = fo.get("matched") or []
    if matched:
        lines = [f"    - scène {m['scene']} : « {m['said']} » → `{m['file']}`" + (f" à {m['at']:.1f}s" if m.get("at") is not None else "") for m in matched[:40]]
        point += f"\n- [ ] Ce qui est dit est montré ({len(matched)} passages) : vérifier que chaque image montre bien ce qui est dit (sinon corrige ses tags avec `python -m gta_factory footage tag`).\n" + "\n".join(lines)
    by_game: dict[str, set[str]] = {}
    for m in fo.get("missing") or []:
        by_game.setdefault(m.get("game") or "gta6", set()).add(m["said"])
    for gid, said in sorted(by_game.items()):
        more = "" if gid == "gta6" else f" --game {gid}"
        name = "" if gid == "gta6" else f" ({(games.get(gid) or {}).get('name', gid)})"
        point += f"\n- [ ] Dit dans la vidéo mais absent de la bibliothèque{name} (une image au hasard a été montrée) : " + ", ".join(f"« {x} »" for x in sorted(said)[:20]) + f". Ajoute des images avec `python -m gta_factory footage add <fichier ou URL> --tags ...{more}`."
    return point


def step_package(ctx: JobContext) -> dict[str, Any]:
    from ...app.core.utils import fmt_duration

    s = ctx.settings
    out = s.outputs_dir / ctx.job_id
    if out.exists():
        shutil.rmtree(out)
    (out / "thumbnails").mkdir(parents=True)
    meta = ctx.need(ctx.paths.metadata)
    video_name = (slugify(meta["title"])[:60] or "video") + ".mp4"
    shutil.copyfile(ctx.paths.final_video, out / video_name)
    for p in sorted(ctx.paths.thumbnails.glob("thumb_*.png")):
        shutil.copyfile(p, out / "thumbnails" / p.name)
    copies = {
        ctx.paths.srt: "captions.srt",
        ctx.paths.vtt: "captions.vtt",
        ctx.paths.metadata: "metadata.json",
        ctx.paths.description: "description.txt",
        ctx.paths.script_md: "script.md",
        ctx.paths.fact_check_report: "fact_check_report.md",
        ctx.paths.scenes: "scene_breakdown.json",
        ctx.paths.rights: "rights_review.json",
        ctx.paths.qc: "qc_report.json",
        ctx.paths.thumbnails / "concepts.json": "thumbnails/concepts.json",
    }
    for src, name in copies.items():
        if src.exists():
            shutil.copyfile(src, out / name)
    write_json(out / "sources.json", _sources(ctx))
    qc = ctx.need(ctx.paths.qc)
    master = ctx.need(ctx.paths.master)
    points = [f"- **{c['severity']}** `{c['id']}` : {c['detail']}" for c in qc["checks"] if not c["passed"] and c["severity"] != "info"]
    write_text(
        out / "REVIEW.md",
        REVIEW_TEMPLATE.format(
            title=meta["title"],
            job_id=ctx.job_id,
            fmt=ctx.fmt,
            duration=fmt_duration(master["total_seconds"]),
            verdict=qc["verdict"].upper(),
            video=video_name,
            lufs=qc.get("measurements", {}).get("lufs") or 0.0,
            n_thumbs=len(list((out / "thumbnails").glob("*.png"))),
            n_titles=len(meta.get("title_options") or []),
            qc_points="\n".join(points) or "Aucun point bloquant ni avertissement.",
        ).replace("- [ ] Vérifier les points du contrôle qualité ci-dessous", "- [ ] Vérifier les points du contrôle qualité ci-dessous" + _footage_point(ctx), 1),
    )
    if ctx.kind == "topic":
        from ...modules.footage.games import facts_path
        from ...modules.topic.produce import review_points as topic_review

        script = ctx.need(ctx.paths.script)
        other = ctx.other_game
        for src, name in ((ctx.paths.outline, "outline.json"), (ctx.paths.game_facts, "game_facts.json")):
            if src.exists():
                shutil.copyfile(src, out / name)
        rel = ""
        if other:
            fp = facts_path(s, other["id"])
            rel = fp.relative_to(s.root).as_posix() if str(fp).startswith(str(s.root)) else str(fp)
        review = (out / "REVIEW.md").read_text(encoding="utf-8")
        review = review.replace("- [ ] Relire `fact_check_report.md` : rien de présenté comme CONFIRMÉ qui ne le soit pas", "- [ ] Relire `fact_check_report.md` : rien de présenté comme CONFIRMÉ qui ne le soit pas" + topic_review(script, ctx.topic_facts(), other, rel), 1)
        write_text(out / "REVIEW.md", review)
        ctx.db.merge_summary(ctx.job_id, duration=master["total_seconds"], video=str(out / video_name), package=str(out))
        ctx.log(f"package ready: {out}")
        return {"package": str(out), "video": video_name, "items": len(script.get("items") or [])}
    if ctx.kind == "details":  # the next details video shows other ones
        from ...modules.details.find import remember_shown
        from ...modules.details.produce import review_points

        details = ctx.need(ctx.paths.details)["details"]
        review = (out / "REVIEW.md").read_text(encoding="utf-8")
        review = review.replace("- [ ] Relire `fact_check_report.md` : rien de présenté comme CONFIRMÉ qui ne le soit pas", "- [ ] Relire `script.md` : la narration ne dit que ce qui se voit" + review_points(details), 1)
        review = review.replace("- `script.md`, `fact_check_report.md` : le script et la vérification de chaque affirmation", "- `script.md` : le script, avec ce que le modèle de vision a vu pour chaque détail", 1)
        write_text(out / "REVIEW.md", review)
        remember_shown(s, details, ctx.job_id)
        ctx.db.merge_summary(ctx.job_id, duration=master["total_seconds"], video=str(out / video_name), package=str(out))
        ctx.log(f"package ready: {out}")
        return {"package": str(out), "video": video_name, "details": len(details)}
    # story memory: the next article about the same events will be recognised
    analysis = ctx.need(ctx.paths.analysis)
    story = ctx.need(ctx.paths.story)
    fact_check = ctx.need(ctx.paths.fact_check)
    ctx.db.add_story(job_id=ctx.job_id, headline=analysis["story_headline"], angle=story["angle"].get("thesis", ""), fmt=ctx.fmt, fingerprint=story["fingerprint"], claims=[c["claim"] for c in fact_check], source_urls=[x["url"] for x in _sources(ctx)])
    added = remember_confirmed_dates(s, fact_check, ctx.job_id)
    ctx.db.merge_summary(ctx.job_id, duration=master["total_seconds"], video=str(out / video_name), package=str(out))
    ctx.log(f"package ready: {out}")
    return {"package": str(out), "video": video_name, "timeline_events_added": added}


STEP_FUNCS: dict[str, Callable[[JobContext], dict[str, Any]]] = {
    "research": step_research,
    "outline": step_outline,
    "details": step_details,
    "details_script": step_details_script,
    "details_scenes": step_details_scenes,
    "extract": step_extract,
    "analyze": step_analyze,
    "corroborate": step_corroborate,
    "fact_check": step_fact_check,
    "select": step_select,
    "script": step_script,
    "scenes": step_scenes,
    "rights": step_rights,
    "voice": step_voice,
    "timeline": step_timeline,
    "render": step_render,
    "mix": step_mix,
    "thumbnail": step_thumbnail,
    "metadata": step_metadata,
    "qc": step_qc,
    "package": step_package,
}


# =========================================================================== jobs
def create_job(settings: Any, urls: list[str], *, fmt: str = "long", options: dict[str, Any] | None = None) -> str:
    fmt_cfg = settings.format(fmt)  # validates the format name
    options = dict(options or {})
    details = options.get("kind") == "details"
    topic = options.get("kind") == "topic"
    if fmt_cfg.get("footage_only") and not details:
        raise ConfigError(f"The '{fmt}' format is made from the footage library, not from articles.", hint="Use `python -m gta_factory details ...`.")
    if fmt_cfg.get("topic_only") and not topic:
        raise ConfigError(f"The '{fmt}' format is a comparison or a top list made from several sources.", hint='Use `python -m gta_factory topic "..."`.')
    urls = [u.strip() for u in urls if u and u.strip()]
    if not urls:
        raise ConfigError("Give at least one article URL.")
    db = get_db(settings.db_path)
    if topic:
        job_id = new_job_id(("vs-" if options.get("style") == "versus" else "top-") + str(options.get("topic") or "topic"))
    else:
        job_id = new_job_id(("details-" if details else "") + urls[0].rstrip("/").rsplit("/", 1)[-1])
    while db.get_job(job_id):
        job_id += "-b"
    paths = JobPaths(settings.jobs_dir / job_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    write_json(paths.job_json, {"id": job_id, "created_at": now_iso(), "urls": urls, "format": fmt, "options": options})
    steps = DETAIL_STEP_NAMES if details else TOPIC_STEP_NAMES if topic else STEP_NAMES
    db.create_job(job_id, urls=urls, fmt=fmt, language=settings.get("channel.language", "en"), options=options, steps=steps, output_dir=str(settings.outputs_dir / job_id))
    return job_id


def create_topic_job(
    settings: Any,
    topic: str,
    *,
    vs: str | None = None,
    sources: list[str] | None = None,
    fmt: str | None = None,
    count: int | None = None,
    options: dict[str, Any] | None = None,
) -> str:
    """A comparison with another game (``vs``: rdr2, gta5 ...) or, without ``vs``, a numbered top list.

    ``sources`` are article URLs (previews, interviews); the news search adds more unless ``no_search``.
    ``fmt``: ``versus`` / ``toplist`` (16:9, the default for each kind) or ``short`` (9:16).
    """
    from ...modules.footage.games import MAIN, load_facts, resolve

    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if len(topic) < 4:
        raise ConfigError("Describe the video in a few words, e.g. \"Red Dead 2 systems inside GTA 6\" or \"12 things to do on day one in GTA 6\".")
    style = "versus" if vs else "list"
    opts = {**(options or {}), "kind": "topic", "topic": topic[:200], "style": style}
    if vs:
        gid = resolve(settings, vs)
        if gid == MAIN:
            raise ConfigError("Compare GTA 6 with another game (--vs rdr2, --vs gta5 ...).")
        load_facts(settings, gid)  # fails now, not after the research, when the facts file is missing
        opts["vs"] = gid
    fmt = fmt or ("versus" if vs else "toplist")
    if fmt in ("versus", "toplist"):
        fmt = "versus" if vs else "toplist"
    elif settings.format(fmt).get("aspect") != "9:16":
        raise ConfigError(f"A comparison or a top list uses the 'versus' / 'toplist' format (16:9) or 'short' (9:16), not '{fmt}'.")
    if count:
        opts["count"] = max(2, min(40, int(count)))
    urls = [u.strip() for u in sources or [] if u and u.strip()]
    if opts.get("text_files"):
        urls += [u for u in opts["text_files"] if u not in urls]
    label = f"topic:{slugify(topic)[:40] or 'topic'}" + (f"-vs-{opts['vs']}" if vs else "")
    return create_job(settings, urls or [label], fmt=fmt, options=opts)


def create_details_job(settings: Any, *, videos: list[str] | None = None, fmt: str = "short", count: int | None = None, options: dict[str, Any] | None = None) -> str:
    """A "details you missed" video made from the footage library (all videos, or those matching ``videos``)."""
    opts = {**(options or {}), "kind": "details", "videos": list(videos or [])}
    if count:
        opts["count"] = int(count)
    label = "-".join(re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-") for v in videos or [])[:40] or "footage"
    return create_job(settings, [f"footage:{label}"], fmt=fmt, options=opts)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobLock:
    """One runner per job (the CLI and the dashboard may both try)."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "JobLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip() or 0)
            except ValueError:
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid):
                raise FactoryError(f"This job is already running (process {pid}).", hint="Wait for it to finish, or stop that process first.")
            self.path.unlink(missing_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.path.unlink(missing_ok=True)


def run_job(settings: Any, job_id: str, *, from_step: str | None = None, until: str | None = None) -> str:
    """Run (or resume) a job. Returns the final job status."""
    db = get_db(settings.db_path)
    job = db.get_job(job_id)
    if job is None:
        raise ConfigError(f"Unknown job '{job_id}'.", hint="List jobs with `python -m gta_factory list`.")
    names = [st["name"] for st in db.get_steps(job_id)] or STEP_NAMES
    if from_step and from_step not in names:
        raise ConfigError(f"Unknown step '{from_step}'.", hint="Steps: " + ", ".join(names))
    if until and until not in names:
        raise ConfigError(f"Unknown step '{until}'.", hint="Steps: " + ", ".join(names))
    paths = JobPaths(settings.jobs_dir / job_id)
    with JobLock(paths.lock):
        if from_step:
            db.reset_steps_from(job_id, from_step)
        elif job["status"] == "stopped" and job.get("current_step"):
            db.reset_steps_from(job_id, job["current_step"])  # re-run the decision (e.g. now with --force)
        else:  # a failed/interrupted step is retried when resuming
            for st in db.get_steps(job_id):
                if st["status"] in ("failed", "running", "skipped"):
                    db.reset_steps_from(job_id, st["name"])
                    break
        db.update_job(job_id, status="running", error=None)
        ctx = JobContext(settings, db, job_id)
        ctx.log(f"=== job {job_id} ({ctx.fmt}) {'resumed from ' + from_step if from_step else 'started'} ===")
        steps = db.get_steps(job_id)
        for i, st in enumerate(steps):
            name = st["name"]
            ctx.step_index = i
            if st["status"] == "done":
                continue
            db.set_step(job_id, name, status="running", started_at=now_iso(), error=None)
            db.update_job(job_id, current_step=name, progress=round(i / len(steps), 4))
            ctx.log(f"--- step {i + 1}/{len(steps)}: {name} ({STEP_TITLES.get(name, name)})")
            t0 = time.time()
            try:
                detail = STEP_FUNCS[name](ctx) or {}
            except NoveltyStop as exc:
                db.set_step(job_id, name, status="done", finished_at=now_iso(), seconds=round(time.time() - t0, 2), detail={"stopped": exc.message})
                for later in steps[i + 1 :]:
                    db.set_step(job_id, later["name"], status="skipped")
                db.update_job(job_id, status="stopped", error=exc.describe(), current_step=name)
                ctx.warn(f"job stopped on purpose: {exc.describe()}")
                ctx.save_llm_usage()
                return "stopped"
            except KeyboardInterrupt:
                db.set_step(job_id, name, status="failed", finished_at=now_iso(), seconds=round(time.time() - t0, 2), error="interrupted")
                db.update_job(job_id, status="failed", error=f"[{name}] interrupted by the operator; resume to continue")
                ctx.save_llm_usage()
                raise
            except FactoryError as exc:
                db.set_step(job_id, name, status="failed", finished_at=now_iso(), seconds=round(time.time() - t0, 2), error=exc.describe())
                db.update_job(job_id, status="failed", error=f"[{name}] {exc.describe()}")
                ctx.logger.error(f"step {name} failed: {exc.describe()}")
                ctx.save_llm_usage()
                return "failed"
            except Exception as exc:  # unexpected bug: keep the traceback in the job log
                tb = traceback.format_exc()
                ctx.logger.error(f"step {name} crashed:\n{tb}")
                msg = f"{type(exc).__name__}: {exc}"
                db.set_step(job_id, name, status="failed", finished_at=now_iso(), seconds=round(time.time() - t0, 2), error=msg)
                db.update_job(job_id, status="failed", error=f"[{name}] unexpected error: {msg} (traceback in pipeline.log)")
                ctx.save_llm_usage()
                return "failed"
            secs = round(time.time() - t0, 2)
            db.set_step(job_id, name, status="done", finished_at=now_iso(), seconds=secs, detail=detail)
            ctx.log(f"    done in {secs:.1f}s")
            ctx.save_llm_usage()
            if until == name and i < len(steps) - 1:
                db.update_job(job_id, status="paused", progress=round((i + 1) / len(steps), 4), current_step=name)
                ctx.log(f"paused after '{name}' as requested; continue with `python -m gta_factory resume {job_id}`")
                return "paused"
        summary = (db.get_job(job_id) or {}).get("summary") or {}
        status = "needs_review" if summary.get("qc_verdict") == "fail" else "completed"
        db.update_job(job_id, status=status, progress=1.0, current_step=None)
        ctx.log(f"=== job {job_id} {status} ===")
        return status


def delete_job(settings: Any, job_id: str, *, keep_outputs: bool = False) -> None:
    db = get_db(settings.db_path)
    db.delete_job(job_id)
    shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
    if not keep_outputs:
        shutil.rmtree(settings.outputs_dir / job_id, ignore_errors=True)
    shutil.rmtree(settings.root / "templates" / ".bundle" / "public" / "job_media" / job_id, ignore_errors=True)
