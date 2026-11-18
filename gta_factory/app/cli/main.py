"""Command line: ``python -m gta_factory <command>``.

    setup                      one-time install: Remotion packages, local LLM (Ollama), voice model, audio library
    doctor                     check every tool and provider
    new URL [URL ...]          create a job from article URL(s) and run it
    details [VIDEO ...]        a "details you missed" video from the footage library (freeze, zoom, ring, label)
    topic "..." [--vs GAME]    a comparison (GTA 6 vs Red Dead 2 / GTA 5, split screen) or a numbered top list
    games                      the games a comparison can use: facts file and footage library of each
    resume JOB [--from STEP]   continue (or redo from a step)
    list | status JOB | log JOB | delete JOB
    rewrite JOB SECTION "..."  regenerate one script section, then resume from "scenes"
    voices                     list the voices of the TTS provider
    brand [IMAGE] [--square]   the channel logo (watermark of every video): install a new one, write the YouTube Studio images
    radar status|scan|tick|worker|list|show|set|sources|schedule
                               GTA VI Content Radar: finds new GTA VI topics and turns them into video ideas
    footage [list|update|add|tag|autotag|find|concepts|remove|fetch|stills] [--game rdr2]
                               real GTA 6 footage library (or another game's, for comparisons): add footage, tag it, see what a sentence shows
    competitors CHANNEL_URL    refresh the competitor reference database
    youtube auth|whoami|logout|status JOB    connect the channel (OAuth, one time)
    publish JOB                upload a reviewed package (private by default, resumable)
    dashboard                  local web dashboard (http://127.0.0.1:8600)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..config.settings import get_settings
from ..core.errors import FactoryError
from ..core.utils import fmt_duration, read_json, which, write_json

STATUS_MARK = {"done": "✓", "failed": "✗", "running": "…", "pending": "·", "skipped": "-"}


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- setup / doctor
def cmd_setup(args: argparse.Namespace) -> int:
    from ...modules.asset_management.audio_library import build_library
    from ...modules.asset_management.models import ensure_kokoro

    s = get_settings()
    missing = [b for b in ("ffmpeg", "ffprobe", "node", "npm") if not which(b)]
    if missing:
        _print(f"missing tools: {', '.join(missing)}  (macOS: brew install ffmpeg node)")
        return 1
    if not (s.root / "node_modules" / "@remotion" / "renderer").exists():
        _print("installing the Remotion renderer (npm install) ...")
        proc = subprocess.run(["npm", "install", "--no-audit", "--no-fund"], cwd=s.root)
        if proc.returncode != 0:
            return proc.returncode
    lib = build_library(s.assets_dir, force=args.force_audio, log=_print)
    _print(f"audio library: {len(lib['generated'])} generated, {len(lib['kept'])} already present")
    if not args.skip_models and s.get("providers.tts.default") == "kokoro":
        ensure_kokoro(s, log=_print)
    llm_ok = True
    if not args.skip_models and s.get("providers.llm.default", "ollama") == "ollama":
        llm_ok = _setup_local_llm(s, args.model)
    (s.root / "knowledge").mkdir(exist_ok=True)
    if not (s.root / ".env").exists() and (s.root / ".env.example").exists():
        _print("tip: copy .env.example to .env only if you use API-key providers (the defaults need no key)")
    if not llm_ok:
        return 1
    _print("setup complete. Next: `python -m gta_factory doctor`")
    return 0


def _setup_local_llm(s: Any, model: str | None) -> bool:
    """Make the free local model ready: Ollama server running and the model downloaded."""
    from ...providers.llm import get_llm
    from ...providers.llm.local import machine_summary

    llm = get_llm(s, "ollama")
    if model:
        llm._model = model  # type: ignore[attr-defined]
    _print(f"local LLM: {machine_summary()}")
    if not llm.start_server(log=_print):  # type: ignore[attr-defined]
        _print(
            "\nOllama is needed for the free local LLM and is not installed (or not running).\n"
            "  macOS : brew install ollama   (or the app from https://ollama.com/download)\n"
            "  Linux : curl -fsSL https://ollama.com/install.sh | sh\n"
            "then run `python -m gta_factory setup` again."
        )
        return False
    tag = llm.model  # type: ignore[attr-defined]
    from ...providers.llm.local import _same_model

    if any(_same_model(tag, h) for h in llm.installed_models()):  # type: ignore[attr-defined]
        _print(f"local LLM: {tag} already downloaded")
    else:
        _print(f"downloading the local model {tag} (one time) ...")
        llm.pull(tag, log=_print)  # type: ignore[attr-defined]
    if model and str(s.get("providers.llm.ollama.model")) not in (model,):
        _print(f"tip: set providers.llm.ollama.model: \"{model}\" in config.yaml to always use it")
    if s.get("footage.enabled", True) and s.get("footage.auto_tag", True):
        from ...modules.footage.autotag import vision_model

        vtag = vision_model(s)
        if any(_same_model(vtag, h) for h in llm.installed_models()):  # type: ignore[attr-defined]
            _print(f"footage tagger: {vtag} already downloaded")
        else:
            _print(f"downloading the local vision model {vtag} (one time, tags the GTA 6 footage so what is said is shown) ...")
            try:
                llm.pull(vtag, log=_print)  # type: ignore[attr-defined]
            except FactoryError as exc:
                _print(f"footage tagger not installed ({exc.describe()}); tag files yourself with `footage tag`, or retry later")
    return True


def cmd_doctor(args: argparse.Namespace) -> int:
    from ...modules.asset_management.audio_library import library_ready
    from ...providers.llm import get_llm
    from ...providers.stock import get_stock_providers
    from ...providers.tts import get_tts

    s = get_settings()
    rows: list[tuple[bool, str, str]] = []
    for b in ("ffmpeg", "ffprobe", "node"):
        rows.append((bool(which(b)), b, which(b) or "not found"))
    rows.append(((s.root / "node_modules" / "@remotion" / "renderer").exists(), "remotion", "node_modules/@remotion" if (s.root / "node_modules").exists() else "run `npm install`"))
    missing = library_ready(s.assets_dir)
    rows.append((not missing, "audio library", "ready" if not missing else f"missing {len(missing)} file(s): run setup"))
    try:
        llm = get_llm(s)
        llm.check()
        detail = llm.describe()
        if args.ping:
            detail += " -> " + llm.complete_text("Answer with one word.", "Say OK.", max_tokens=20, label="ping")[:30]
        rows.append((True, "LLM", detail))
    except FactoryError as exc:
        rows.append((False, "LLM", exc.describe()))
    try:
        tts = get_tts(s)
        tts.check()
        rows.append((True, "voice", f"{tts.name} / {tts.voice_id()}"))
    except FactoryError as exc:
        rows.append((False, "voice", exc.describe()))
    try:
        import faster_whisper  # noqa: F401

        rows.append((True, "subtitle alignment", "faster-whisper (word timings from the audio)"))
    except ImportError:
        rows.append((True, "subtitle alignment", "sentence heuristic (pip install faster-whisper for word-exact timing)"))
    stock = [p.name for p in get_stock_providers(s)]
    rows.append((True, "stock b-roll", ", ".join(stock) if stock else "off (no PEXELS/PIXABAY key; scenes use original graphics)"))
    rows.append((True, "rights mode", str(s.get("rights_review.mode"))))
    try:
        from ...modules.footage.library import build_index, library_summary

        from ...modules.footage.select import ratio_band

        fs = library_summary(build_index(s)) if s.get("footage.enabled", True) else None
        _, lo, hi = ratio_band(s)
        rows.append((True, "GTA 6 footage", "off (footage.enabled: false)" if fs is None else (f"{fs['videos']} video(s), {fs['usable_shots']} shots, {fs['photos']} photo(s) - {lo:.0%}-{hi:.0%} of each video" if fs["usable_shots"] or fs["photos"] else "empty: run `python -m gta_factory footage update` for real trailer footage")))
        if fs is not None and s.get("footage.auto_tag", True):
            from ...modules.footage.autotag import pending, vision_status

            ready, detail = vision_status(s)
            left = len(pending(s)) if fs["usable_shots"] or fs["photos"] else 0
            rows.append((True, "footage tagger", detail + (f" - {left} shot(s)/photo(s) not tagged yet: `python -m gta_factory footage autotag`" if ready and left else "")))
    except FactoryError as exc:
        rows.append((True, "GTA 6 footage", f"not indexed ({exc.message})"))
    try:  # the games a comparison can put next to GTA 6 (python -m gta_factory topic ... --vs GAME)
        from ...modules.footage.games import MAIN, game_settings, games, load_facts
        from ...modules.footage.library import build_index, library_summary

        parts = []
        for gid in (g for g in games(s) if g != MAIN):
            fs = library_summary(build_index(game_settings(s, gid)))
            try:
                facts = f"{len(load_facts(s, gid))} facts"
            except FactoryError:
                facts = "no facts file"
            parts.append(f"{gid}: {facts}, " + (f"{fs['videos']} video(s)" if fs["usable_shots"] or fs["photos"] else f"no footage (footage update --game {gid})"))
        rows.append((True, "comparisons", "; ".join(parts) or "no other game in config.yaml (games)"))
    except FactoryError as exc:
        rows.append((True, "comparisons", exc.describe()))
    paid = [x for x, on in (("Claude subscription", s.get("providers.llm.default") == "claude_code"), ("Anthropic API", s.get("providers.llm.default") == "anthropic"), ("OpenAI API", s.get("providers.llm.default") == "openai" or s.get("providers.tts.default") == "openai"), ("ElevenLabs", s.get("providers.tts.default") == "elevenlabs")) if on]
    rows.append((True, "cost", "0 $ - everything runs locally, no account or subscription" if not paid else "uses " + ", ".join(paid)))
    rows.append((True, "youtube upload", "enabled" if s.get("youtube.upload_enabled") else "off (packages are produced for review)"))
    ok = all(r[0] for r in rows)
    for good, name, detail in rows:
        _print(f"  {'✓' if good else '✗'} {name:<20} {detail}")
    _print("\nall good." if ok else "\nfix the ✗ lines (see README.md).")
    return 0 if ok else 1


# --------------------------------------------------------------------------- jobs
def _options(args: argparse.Namespace) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if getattr(args, "text_file", None):
        tf = {}
        for item in args.text_file:
            if "=" not in item:
                raise FactoryError(f"--text-file expects URL=PATH, got '{item}'")
            url, path = item.split("=", 1)
            tf[url.strip()] = str(Path(path).expanduser().resolve())
        opts["text_files"] = tf
    for key in ("force", "no_search"):
        if getattr(args, key, False):
            opts[key] = True
    if getattr(args, "no_audit", False):
        opts["audit"] = False
    if getattr(args, "no_whisper", False):
        opts["whisper"] = False
    for key in ("llm", "tts", "voice", "today", "target_seconds"):
        if getattr(args, key, None):
            opts[key] = getattr(args, key)
    if getattr(args, "broll", None) is not None:
        opts["allow_broll"] = args.broll
    return opts


def _run(job_id: str, *, from_step: str | None = None, until: str | None = None) -> int:
    from ..core.pipeline import run_job

    s = get_settings()
    status = run_job(s, job_id, from_step=from_step, until=until)
    _print("")
    _show_status(job_id)
    return 0 if status in ("completed", "paused", "stopped") else 2 if status == "needs_review" else 1


def cmd_new(args: argparse.Namespace) -> int:
    from ..core.pipeline import create_job

    s = get_settings()
    urls = list(args.urls)
    if args.text_file:
        urls += [i.split("=", 1)[0] for i in args.text_file if i.split("=", 1)[0] not in urls]
    job_id = create_job(s, urls, fmt=args.format, options=_options(args))
    _print(f"job {job_id} created ({args.format})")
    if args.detach:
        log = open(s.jobs_dir / job_id / "pipeline.out", "ab")
        subprocess.Popen([sys.executable, "-m", "gta_factory", "resume", job_id] + (["--until", args.until] if args.until else []), cwd=s.root, stdout=log, stderr=log, start_new_session=True)
        _print(f"running in the background; follow with `python -m gta_factory log {job_id} -f`")
        return 0
    return _run(job_id, until=args.until)


def cmd_details(args: argparse.Namespace) -> int:
    from ..core.pipeline import create_details_job

    s = get_settings()
    videos = list(args.videos)
    if args.scan_only:
        from ...modules.details.find import make_verifier, pick_details, scan

        cands = scan(s, videos=videos or None, model=args.model, limit=args.limit, log=_print)
        picked = pick_details(s, cands, args.count or 15, verify=make_verifier(s, model=args.model, log=_print))
        _print(f"\n{len(cands)} possible detail(s) found; the best {len(picked)}, checked in close-up:")
        for d in picked:
            _print(f"  #{d['id'][1:]:<3} {d['video_title']} {d['timecode']:>6}  {d['label']}" + (f"  « {d['text']} »" if d.get("text") else "") + f"  - {d['what']}")
        if picked:
            _print(f"\nmake the video: python -m gta_factory details {' '.join(repr(v) for v in videos)} --format short".rstrip())
        return 0
    opts = _options(args)
    if args.model:
        opts["vision_model"] = args.model
    job_id = create_details_job(s, videos=videos, fmt=args.format, count=args.count, options=opts)
    _print(f"job {job_id} created (details you missed, {args.format})")
    if args.detach:
        log = open(s.jobs_dir / job_id / "pipeline.out", "ab")
        subprocess.Popen([sys.executable, "-m", "gta_factory", "resume", job_id] + (["--until", args.until] if args.until else []), cwd=s.root, stdout=log, stderr=log, start_new_session=True)
        _print(f"running in the background; follow with `python -m gta_factory log {job_id} -f`")
        return 0
    return _run(job_id, until=args.until)


def cmd_topic(args: argparse.Namespace) -> int:
    from ..core.pipeline import create_topic_job

    s = get_settings()
    job_id = create_topic_job(s, args.topic, vs=args.vs, sources=list(args.source or []), fmt=args.format, count=args.count, options=_options(args))
    _print(f"job {job_id} created ({'comparison with ' + args.vs if args.vs else 'top list'})")
    if args.detach:
        log = open(s.jobs_dir / job_id / "pipeline.out", "ab")
        subprocess.Popen([sys.executable, "-m", "gta_factory", "resume", job_id] + (["--until", args.until] if args.until else []), cwd=s.root, stdout=log, stderr=log, start_new_session=True)
        _print(f"running in the background; follow with `python -m gta_factory log {job_id} -f`")
        return 0
    return _run(job_id, until=args.until)


def cmd_games(args: argparse.Namespace) -> int:
    from ...modules.footage import library as lib
    from ...modules.footage.games import MAIN, facts_path, game_settings, games, load_facts

    s = get_settings()
    for gid, g in games(s).items():
        gs = game_settings(s, gid)
        summ = lib.library_summary(lib.build_index(gs))
        foot = f"{summ['videos']} video(s), {summ['usable_shots']} shots, {summ['photos']} photo(s)" if summ["usable_shots"] or summ["photos"] else f"empty: python -m gta_factory footage update{'' if gid == MAIN else ' --game ' + gid}"
        if gid == MAIN:
            _print(f"  {gid:<6} {g['name']:<26} footage: {foot}")
            continue
        try:
            facts = f"{len(load_facts(s, gid))} facts ({facts_path(s, gid).relative_to(s.root) if str(facts_path(s, gid)).startswith(str(s.root)) else facts_path(s, gid)})"
        except FactoryError as exc:
            facts = f"no facts file ({exc.message})"
        _print(f"  {gid:<6} {g['name']:<26} {facts}; footage: {foot}")
    _print('\ncompare: python -m gta_factory topic "Red Dead 2 systems inside GTA 6" --vs rdr2 --source <preview URL>')
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from ..database.db import get_db

    s = get_settings()
    db = get_db(s.db_path)
    job = db.get_job(args.job)
    if job is None:
        raise FactoryError(f"Unknown job '{args.job}'.", hint="List jobs with `python -m gta_factory list`.")
    opts = dict(job.get("options") or {})
    changed = {k: v for k, v in _options(args).items() if opts.get(k) != v}
    if changed:
        opts.update(changed)
        db.update_job(args.job, options=opts)
        _print(f"options updated: {', '.join(changed)}")
    return _run(args.job, from_step=args.from_step, until=args.until)


def _show_status(job_id: str) -> None:
    from ..database.db import get_db

    s = get_settings()
    db = get_db(s.db_path)
    job = db.get_job(job_id)
    if not job:
        raise FactoryError(f"Unknown job '{job_id}'.")
    summary = job.get("summary") or {}
    _print(f"{job['id']}  [{job['status'].upper()}]  {job['format']}  {job.get('title') or ''}")
    if summary.get("novelty_score") is not None:
        _print(f"  novelty {summary['novelty_score']}/100 ({summary.get('decision')})  qc {summary.get('qc_verdict', '-')}" + (f"  duration {fmt_duration(summary['duration'])}" if summary.get("duration") else ""))
    for st in db.get_steps(job_id):
        secs = f"{st['seconds']:.0f}s" if st.get("seconds") else ""
        extra = st.get("error") or ""
        if not extra and isinstance(st.get("detail"), dict):
            extra = ", ".join(f"{k}={v}" for k, v in st["detail"].items() if isinstance(v, (int, float, str)) and len(str(v)) < 60)[:110]
        _print(f"  {STATUS_MARK.get(st['status'], '?')} {st['name']:<12} {secs:>5}  {extra}")
    if job.get("error"):
        _print(f"\n  {job['error']}")
    if summary.get("package"):
        _print(f"\n  package: {summary['package']}")


def cmd_status(args: argparse.Namespace) -> int:
    _show_status(args.job)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from ..database.db import get_db

    s = get_settings()
    jobs = get_db(s.db_path).list_jobs(args.limit)
    if not jobs:
        _print("no jobs yet. Start one with: python -m gta_factory new <article-url>")
        return 0
    for j in jobs:
        summ = j.get("summary") or {}
        _print(f"{j['id']:<44} {j['status']:<12} {j['format']:<12} {str(summ.get('novelty_score', '')):>3}  {(j.get('title') or '')[:60]}")
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    s = get_settings()
    path = s.jobs_dir / args.job / "pipeline.log"
    if not path.exists():
        raise FactoryError(f"No log for job '{args.job}'.")
    if args.follow:
        return subprocess.call(["tail", "-n", str(args.lines), "-f", str(path)])
    lines = path.read_text(encoding="utf-8").splitlines()
    _print("\n".join(lines[-args.lines :]))
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    from ..core.pipeline import delete_job

    if not args.yes:
        answer = input(f"delete job {args.job} and its files{'' if args.keep_outputs else ' (including outputs/)'}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "o", "oui"):
            return 1
    delete_job(get_settings(), args.job, keep_outputs=args.keep_outputs)
    _print("deleted.")
    return 0


def cmd_rewrite(args: argparse.Namespace) -> int:
    from ...modules.script_generation.writer import render_script_markdown, rewrite_section
    from ...providers.llm import get_llm

    s = get_settings()
    root = s.jobs_dir / args.job
    script = read_json(root / "script" / "script.json")
    if not script:
        raise FactoryError("This job has no script yet.")
    fact_check = read_json(root / "research" / "fact_check.json", [])
    analysis = read_json(root / "research" / "analysis.json", {})
    if script.get("kind") == "topic":  # the other game's facts may be used too
        from ...modules.topic.produce import job_facts

        fact_check = job_facts(s, args.job, fact_check)
    llm = get_llm(s)
    script = rewrite_section(llm, script=script, section_id=args.section, instruction=args.instruction, fact_check=fact_check, quotes=analysis.get("quotes") or [])
    write_json(root / "script" / "script.json", script)
    if script.get("kind") == "topic":
        from ...modules.topic.writer import script_markdown

        (root / "script" / "script.md").write_text(script_markdown(script, fact_check), encoding="utf-8")
    else:
        (root / "script" / "script.md").write_text(render_script_markdown(script, read_json(root / "story" / "story.json", {}), fact_check), encoding="utf-8")
    sec = next(x for x in script["sections"] if x["id"] == args.section)
    _print(f"[{args.section}] {sec['narration']}\n")
    _print(f"next: python -m gta_factory resume {args.job} --from scenes")
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    from ...modules.voice_generation.samples import KOKORO_PICKS, sample
    from ...providers.tts import get_tts

    s = get_settings()
    if not args.sample:
        tts = get_tts(s, args.provider)
        for v in tts.list_voices():
            _print(v)
        return 0
    # the same sentence read by several voices, to listen and choose (outputs/voice_samples/)
    provider = args.provider or s.get("providers.tts.default", "kokoro")
    voices = list(args.voices) or ([v for v, _ in KOKORO_PICKS] if provider == "kokoro" else [str((s.get(f"providers.tts.{provider}") or {}).get("voice") or (s.get(f"providers.tts.{provider}") or {}).get("voice_id") or "")])
    out = s.outputs_dir / "voice_samples"
    for v in voices:
        try:
            _print(f"  {provider} {v or '(default)'}: {sample(s, provider, v, out_dir=out)}")
        except FactoryError as exc:
            _print(f"  {provider} {v}: {exc.describe()}")
    _print(f"\nlisten, then choose: providers.tts.default and providers.tts.{provider}.{'voice_id' if provider == 'elevenlabs' else 'voice'} in config.yaml, or --tts/--voice for one job (also in the dashboard)")
    return 0


def _tag_list(raw: str | None) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _autotag_new(s: Any, args: argparse.Namespace) -> None:
    """Tag what was just added, when the free local vision model is available (never fails the command)."""
    if getattr(args, "no_autotag", False) or not s.get("footage.auto_tag", True):
        return
    from ...modules.footage.autotag import autotag

    try:
        autotag(s, log=_print)
    except FactoryError as exc:
        _print(f"auto-tagging skipped: {exc.describe()}")


def _footage_listing(s: Any, index: dict[str, Any]) -> None:
    from ...modules.footage import library as lib
    from ...modules.footage.select import ratio_band

    items = lib.load_items(s, index)
    summ = lib.library_summary(index)
    from ...modules.footage.games import footage_label

    _print(f"\n{footage_label(s)} footage library: {lib.footage_dir(s)}")
    tagged = lambda its, base=(): sum(1 for it in its if set(it["tags"]) - set(base))  # noqa: E731
    for v in index["videos"]:
        its = [it for it in items["shots"] if it["file"] == v["file"]]
        _print(f"  video  {v['file']}  {v['duration']:.0f}s  {len(its)} usable shots ({tagged(its, v['tags'])} tagged)  tags: {', '.join(v['tags']) or '-'}")
    if index["photos"]:
        _print(f"  photos {len(index['photos'])} ({tagged(items['photos'])} tagged): {', '.join(p['file'] for p in index['photos'][:4])}{' ...' if len(index['photos']) > 4 else ''}")
    for sk in summ["skipped"]:
        _print(f"  skipped {sk['file']}: {sk['reason']}")
    _print(f"total: {summ['videos']} video(s), {summ['usable_shots']} usable shots ({summ['usable_seconds']:.0f}s), {summ['photos']} photo(s)")
    if not summ["usable_shots"] and not summ["photos"]:
        gid = getattr(s, "game_id", "gta6")
        more = f" --game {gid}" if gid != "gta6" else ""
        _print(f"the library is empty: run `python -m gta_factory footage update{more}` (official trailers) or `footage add <files, folder or URL>{more}`")
        return
    cov = lib.concept_coverage(s, items)
    have = [f"{r['concept']} ({r['shots'] + r['photos']})" for r in cov if r["shots"] + r["photos"]]
    _print(f"what the narration can call for: {', '.join(have) if have else 'nothing tagged yet - run `footage autotag` or tag files with `footage tag`'}")
    _, lo, hi = ratio_band(s)
    _print(f"new videos show real footage on {lo:.0%}-{hi:.0%} of the running time (footage.ratio_min / ratio_max in config.yaml)")


def cmd_brand(args: argparse.Namespace) -> int:
    from ...modules.branding import BRAND_DIR, PROFILE_NAME, WATERMARK_NAME, install_logo, logo_file

    s = get_settings()
    if args.image:
        out = install_logo(s, Path(args.image).expanduser(), square=args.square)
        _print(f"logo installed: {out['logo']}")
        configured = str(s.get("channel.logo") or "")
        if Path(configured) != BRAND_DIR / "logo.png" and Path(configured).expanduser() != out["logo"]:
            _print(f'  note: config.yaml uses channel.logo: "{configured}". Set it to "assets/brand/logo.png" to use the new logo.')
    logo = logo_file(s)
    _print(f"channel: {s.get('channel.name', '')} - {s.get('channel.tagline', '')}")
    _print(f"logo (watermark of every video, end screen, thumbnails): {logo or 'none (channel.logo is empty)'}")
    folder = s.root / BRAND_DIR
    if (folder / PROFILE_NAME).exists():
        _print("YouTube Studio > Customization > Branding:")
        _print(f"  Picture          {folder / PROFILE_NAME}")
        _print(f"  Video watermark  {folder / WATERMARK_NAME}  (display: entire video)")
    _print("Videos already rendered keep their old picture: resume them from the render step to update them.")
    return 0


def cmd_radar(args: argparse.Namespace) -> int:
    from ...modules.radar import engine
    from ...modules.radar.store import STATUSES, RadarStore

    s = get_settings()
    store = RadarStore.of(s)
    store.seed_sources(engine.configured_sources(s))
    action = args.action or "status"
    if action == "scan":
        res = engine.run_scan(s, trigger=args.trigger, log=_radar_log(s))
        _print(json.dumps({k: v for k, v in res.items() if k != "errors"}, indent=1))
        for e in res.get("errors") or []:
            _print(f"  ! {e['source']}: {e['error']}")
        return 0 if res["status"] in ("success", "partial", "busy") else 1
    if action == "tick":  # what launchd / cron call every 15 minutes: scans only when one is due
        res = engine.tick(s, via=args.via, log=_radar_log(s))
        _print("radar: nothing due" if res is None else f"radar: scan {res['status']}")
        return 0
    if action == "worker":
        from ...modules.radar.worker import run_forever

        _print(f"radar worker: checking every minute, scanning every {engine.config(s, store)['interval_hours']} h (Ctrl+C to stop)")
        run_forever(s, via="worker")
        return 0
    if action == "schedule":
        from ...modules.radar.worker import ensure_schedule, install_schedule, schedule_status, uninstall_schedule

        sub = (args.args or ["status"])[0]
        if sub == "install":
            _print(install_schedule(s))
        elif sub == "ensure":  # start.sh: install once
            _print(ensure_schedule(s) if s.get("radar.install_service", True) and engine.config(s, store)["enabled"] else "background service not wanted (radar.install_service: false)")
        elif sub == "uninstall":
            _print(uninstall_schedule(s))
        else:
            _print(json.dumps(schedule_status(s), indent=1))
        return 0
    if action == "sources":
        sub = (args.args or ["list"])[0]
        if sub == "add":
            from ...modules.radar.sources import normalize_source

            src = normalize_source({"kind": args.kind, "target": " ".join(args.args[1:]), "name": args.name, "trust": args.trust, "topic_only": args.topic_only})
            row = store.add_source(**src)
            _print(f"added source #{row['id']}: {row['name']} ({row['kind']}: {row['target']})")
            return 0
        if sub in ("enable", "disable", "remove") and len(args.args) > 1:
            sid = int(args.args[1])
            if sub == "remove":
                store.delete_source(sid)
            else:
                store.update_source(sid, enabled=int(sub == "enable"), next_attempt_at=None)
            _print(f"source #{sid}: {sub}d")
            return 0
        for src in store.list_sources():
            health = "off" if not src["enabled"] else ("ok" if not src.get("last_error") else f"FAILING x{src['consecutive_failures']}: {src['last_error'][:60]}")
            _print(f"#{src['id']:<3} {src['trust']:<9} {src['kind']:<11} {src['name'][:34]:<34} {health}")
        return 0
    if action == "list":
        rows, total = store.list_opportunities(status=args.status, include_low=args.all, limit=args.limit)
        for o in rows:
            _print(f"#{o['id']:<4} {str(o.get('score') if o.get('score') is not None else '-'):>3} {str(o.get('priority') or o['analysis_status']):<8} {o['status']:<9} {str(o.get('format') or '-'):<5} {o['title'][:80]}")
        _print(f"({len(rows)} of {total})")
        return 0
    if action == "show":
        from ...modules.radar.api import opportunity_payload

        _print(json.dumps(opportunity_payload(s, int(args.args[0])), indent=1, ensure_ascii=False))
        return 0
    if action == "set":
        oid, status = int(args.args[0]), args.args[1]
        if status not in STATUSES:
            raise FactoryError(f"unknown status '{status}'", hint="Use one of: " + ", ".join(STATUSES))
        store.set_status(oid, status)
        _print(f"#{oid}: {status}")
        return 0
    info = engine.schedule(s, store)
    from ...modules.radar.worker import schedule_status

    c = store.counts()
    _print(f"radar: {info['status']} · every {info['interval_hours']} h · last success {info['last_success_at'] or 'never'} · next {info['next_scan_at'] or '-'}")
    _print(f"  {c['discoveries']} discoveries · {c['new']} new · {c['high_priority']} high priority · {c['pending_analysis']} waiting for analysis · {len(store.list_sources())} sources")
    sched = schedule_status(s)
    _print(f"  system schedule ({sched['system']}): {'installed' if sched['installed'] else 'not installed - run `python -m gta_factory radar schedule install` to scan with the dashboard closed'}")
    return 0


def _radar_log(settings: Any):
    from ...modules.radar.engine import radar_logger

    return radar_logger(settings)


def cmd_footage(args: argparse.Namespace) -> int:
    from ...modules.footage import library as lib
    from ...modules.footage.games import game_settings

    s = game_settings(get_settings(), args.game)  # --game rdr2: that game's library, for comparisons
    tags = _tag_list(args.tags)
    act = args.action
    if act == "update":
        try:
            lib.fetch(s, list(args.paths) or None, max_height=args.max_height, log=_print)
        except FactoryError as exc:  # the known trailers failing must not stop the search for new ones
            _print(f"footage: {exc.describe()}")
        if not args.no_new:
            try:
                new = lib.find_new_official(s, log=_print)
            except FactoryError as exc:
                new = []
                _print(f"could not look for new videos: {exc.describe()}")
            if new:
                for n in new:
                    _print(f"  new: {n['title']}")
                lib.fetch(s, [n["url"] for n in new], max_height=args.max_height, log=_print)
            else:
                _print("footage: no new official GTA VI video")
        if not args.no_stills:
            lib.extract_stills(s, per_video=args.stills, log=_print)
        _autotag_new(s, args)
    elif act == "fetch":
        lib.fetch(s, args.paths or None, max_height=args.max_height, tags=tags, log=_print)
        if not args.no_stills:
            lib.extract_stills(s, per_video=args.stills, log=_print)
        _autotag_new(s, args)
    elif act == "add":
        if not args.paths:
            _print("usage: footage add FILE_FOLDER_OR_URL [...] [--tags lucia,beach]")
            return 2
        added = lib.add_files(s, list(args.paths), tags=tags, credit=args.credit or "", log=_print)
        if not added:
            _print("nothing added (supported: mp4 mov webm mkv m4v / jpg jpeg png webp, or a video / image URL)")
            return 1
        if any(p.suffix.lower() in lib.VIDEO_EXT for p in added) and not args.no_stills:
            lib.extract_stills(s, per_video=args.stills, log=_print)
        _autotag_new(s, args)
    elif act == "tag":
        if not args.paths or not (tags or args.remove or args.clear):
            _print("usage: footage tag FILE --tags lucia,beach [--at 12-19.5] [--remove police] [--clear]")
            return 2
        path = lib.resolve_file(s, " ".join(args.paths))
        at = None
        if args.at:
            try:
                a, b = (float(x) for x in args.at.replace(",", ".").split("-", 1))
            except ValueError:
                raise FactoryError("--at expects START-END in seconds, e.g. 12-19.5")
            at = (a, b)
        side = lib.set_tags(s, path, add=tags, remove=_tag_list(args.remove), replace=[] if args.clear else None, at=at)
        where = f" from {at[0]:g}s to {at[1]:g}s" if at else ""
        now = next((x["tags"] for x in side.get("segments", []) if at and abs(x["start"] - at[0]) < 0.05), []) if at else side.get("tags", [])
        _print(f"{path.name}{where}: {', '.join(now) or '(no tags)'}")
        return 0
    elif act == "autotag":
        from ...modules.footage.autotag import autotag

        autotag(s, force=args.force, limit=args.limit, model=args.model, log=_print)
    elif act == "find":
        from ...modules.footage.select import preview

        text = " ".join(args.paths)
        if not text:
            _print('usage: footage find "Lucia escapes the cops in Vice City"')
            return 2
        rows = preview(s, text)
        if not rows:
            _print("nothing in this sentence calls for specific footage: any GTA 6 footage will be shown")
        for r in rows:
            if r["matches"]:
                _print(f"« {r['said']} » ({', '.join(r['concepts'])}): {r['matches']} match(es)")
                for b in r["best"]:
                    _print(f"    {b['file']}  [{', '.join(b['tags'][:8])}]")
            else:
                _print(f"« {r['said']} » ({', '.join(r['concepts'])}): nothing in the library - add some with `footage add <file or URL> --tags {r['concepts'][0]}`")
        return 0
    elif act == "concepts":
        from ...modules.footage.vocabulary import load_vocabulary, tags_file

        vocab = load_vocabulary(s)
        cov = {r["concept"]: r for r in lib.concept_coverage(s)}
        _print(f"concepts the narration can call for (add yours in {tags_file(s)}):")
        for cid, c in vocab.concepts.items():
            r = cov.get(cid) or {"shots": 0, "photos": 0}
            n = r["shots"] + r["photos"]
            _print(f"  {'✓' if n else '·'} {cid:<16} {('%d shot(s), %d photo(s)' % (r['shots'], r['photos'])) if n else 'no footage yet':<24} says: {', '.join((c.get('say') or [])[:5])}")
        return 0
    elif act == "remove":
        if not args.paths:
            _print("usage: footage remove FILE")
            return 2
        path = lib.resolve_file(s, " ".join(args.paths))
        if not args.yes and input(f"remove {path.relative_to(lib.footage_dir(s))} from the library? [y/N] ").strip().lower() not in ("y", "yes", "o", "oui"):
            return 1
        lib.remove_file(s, path)
        _print(f"removed {path.name}")
    elif act == "stills":
        lib.extract_stills(s, per_video=args.stills, log=_print)
        _autotag_new(s, args)
    index = lib.build_index(s, force=args.reindex, log=_print)
    _footage_listing(s, index)
    return 0


def cmd_competitors(args: argparse.Namespace) -> int:
    from ...modules.competitor_analysis.analyze import run
    from ...modules.competitor_analysis.collect import collect_channel

    s = get_settings()
    cache = s.root / "reference_analysis" / ".cache"
    collect_channel(args.channel_url, cache, refresh=args.refresh, progress=_print)
    db = run(cache, s.root / "reference_analysis")
    stats = {k: v for k, v in db.get("statistics", {}).items() if k != "thumbnails"}
    _print(json.dumps(stats, indent=1)[:3000])
    _print(f"\nwritten: {s.root / 'reference_analysis' / 'competitor_video_database.json'}")
    return 0


def cmd_youtube(args: argparse.Namespace) -> int:
    from ...modules.publishing import youtube as yt

    s = get_settings()
    if args.action == "auth":
        ch = yt.authorize(s, open_browser=not args.no_browser, log=_print)
        _print(f"connected to the channel '{ch['title']}' ({ch['id']}). Token saved in {yt.token_path(s)} (keep it private).")
    elif args.action == "whoami":
        client = yt.YouTubeClient(s)
        try:
            ch = client.channel()
        finally:
            client.close()
        _print(f"{ch['title']}  {ch['custom_url']}  ({ch['id']}, {ch.get('subscribers') or '?'} subscribers)")
    elif args.action == "logout":
        _print("access revoked and token deleted." if yt.logout(s) else "not connected.")
    elif args.action == "status":
        if not args.job:
            raise FactoryError("Give the job: python -m gta_factory youtube status <job>")
        rec = yt.refresh_status(s, args.job)
        _print(json.dumps({k: rec.get(k) for k in ("url", "studio_url", "privacy", "publish_at", "steps", "youtube_status")}, indent=1, ensure_ascii=False))
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from ...modules.publishing import youtube as yt

    s = get_settings()
    pkg = yt.Package(s, args.job)
    items = yt.review_checklist(pkg.review)
    if items and not all(i["done"] for i in items) and not args.dry_run:
        _print("REVIEW.md checklist:")
        for i in items:
            _print(f"  [{'x' if i['done'] else ' '}] {i['text']}")
        confirmed = args.reviewed
        if not confirmed and sys.stdin.isatty():
            confirmed = input("Did you do all of the above yourself? [y/N] ").strip().lower() in ("y", "yes", "o", "oui")
        if not confirmed:
            raise FactoryError("The package has not been reviewed.", hint="Tick the boxes in REVIEW.md, or pass --reviewed once you have done the review.")
        yt.set_review_checklist(pkg.review, True)
    res = yt.publish(
        s,
        args.job,
        title_index=args.title,
        title_text=args.title_text,
        thumbnail=args.thumbnail,
        privacy=args.privacy,
        publish_at=args.publish_at,
        overrides=args.override_qc or [],
        again=args.again,
        dry_run=args.dry_run,
        log=_print,
    )
    if args.dry_run:
        _print(json.dumps(res, indent=1, ensure_ascii=False))
        return 0
    _print(f"\nuploaded: {res['url']}  ({res['privacy']}{', scheduled ' + res['publish_at'] if res.get('publish_at') else ''})")
    _print(f"YouTube Studio: {res['studio_url']}")
    for step, state in res.get("steps", {}).items():
        if state != "done":
            _print(f"  ! {step}: {state}")
    meta = pkg.metadata
    if meta.get("pinned_comment"):
        _print(f"\nto pin by hand (the API cannot pin comments): {meta['pinned_comment']}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from ..dashboard.server import serve

    s = get_settings()
    serve(s, host=args.host or s.get("dashboard.host", "127.0.0.1"), port=args.port or int(s.get("dashboard.port", 8600)))
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    from ..core.pipeline import ALL_STEP_NAMES as STEP_NAMES

    p = argparse.ArgumentParser(prog="python -m gta_factory", description="GTA 6 video factory: verified news videos from article URLs.")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("setup", help="one-time install (Remotion, voice model, audio library)")
    sp.add_argument("--skip-models", action="store_true", help="do not download the voice model nor the local LLM")
    sp.add_argument("--force-audio", action="store_true", help="regenerate the music beds and sound effects")
    sp.add_argument("--model", help="local LLM to download instead of the automatic choice (e.g. qwen3:8b)")
    sp.set_defaults(fn=cmd_setup)

    sp = sub.add_parser("doctor", help="check tools and providers")
    sp.add_argument("--ping", action="store_true", help="also send a tiny request to the LLM")
    sp.set_defaults(fn=cmd_doctor)

    def job_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--text-file", action="append", metavar="URL=PATH", help="use saved article text for a paywalled/blocked URL (repeatable)")
        sp.add_argument("--force", action="store_true", help="produce even if the story is a duplicate or scores below the novelty threshold")
        sp.add_argument("--no-search", action="store_true", help="skip the corroboration search")
        sp.add_argument("--no-audit", action="store_true", help="skip the standards-editor audit of the script (faster, less safe)")
        sp.add_argument("--no-whisper", action="store_true", help="skip audio-based subtitle alignment")
        sp.add_argument("--llm", help="LLM provider for this job (ollama = free local default, claude_code, anthropic, openai)")
        sp.add_argument("--tts", help="voice provider for this job (kokoro, elevenlabs, openai)")
        sp.add_argument("--voice", help="voice name / id for this job")
        sp.add_argument("--today", help="override today's date (YYYY-MM-DD)")
        sp.add_argument("--target-seconds", type=float, help="narration length target")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--broll", dest="broll", action="store_true", default=None, help="allow licensed b-roll scenes")
        g.add_argument("--no-broll", dest="broll", action="store_false", help="original graphics only")
        sp.add_argument("--until", choices=STEP_NAMES, help="pause after this step (e.g. 'script' to review the script first)")

    sp = sub.add_parser("new", help="create a job from article URL(s) and run it")
    sp.add_argument("urls", nargs="*", help="article URL(s) about one story")
    sp.add_argument("--format", "-f", default="long", help="long | breaking | announcement | recap | analysis | guide | short")
    sp.add_argument("--detach", action="store_true", help="run in the background")
    job_opts(sp)
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("details", help='a "details you missed" video made from the footage library')
    sp.add_argument("videos", nargs="*", help='which footage: part of a file name or a tag (e.g. "Trailer 2", gameplay); default: every video')
    sp.add_argument("--format", "-f", default="short", choices=("short", "details"), help="short = vertical Short (default), details = 16:9 video")
    sp.add_argument("--count", "-n", type=int, help="how many details (default: details.count_short / count_long)")
    sp.add_argument("--scan-only", action="store_true", help="only look for details and list the best ones (no video)")
    sp.add_argument("--model", help="Ollama vision model (default footage.vision_model)")
    sp.add_argument("--limit", type=int, help="--scan-only: look at most this many new frames")
    sp.add_argument("--detach", action="store_true", help="run in the background")
    job_opts(sp)
    sp.set_defaults(fn=cmd_details)

    sp = sub.add_parser("topic", help="a comparison (GTA 6 vs another game, split screen) or a numbered top list, from several sources")
    sp.add_argument("topic", help='what the video is about, e.g. "Red Dead 2 systems inside GTA 6" or "12 things to do on day one in GTA 6"')
    sp.add_argument("--vs", help="compare GTA 6 with this game: rdr2, gta5 ... (`python -m gta_factory games`); without --vs: a numbered top list")
    sp.add_argument("--source", "-s", action="append", metavar="URL", help="an article to use: preview, interview, Rockstar Newswire ... (repeatable; the news search adds more unless --no-search)")
    sp.add_argument("--count", "-n", type=int, help="number of items (default: topic.count_long / count_short)")
    sp.add_argument("--format", "-f", choices=("versus", "toplist", "short"), help="16:9 video (default) or short = vertical Short")
    sp.add_argument("--detach", action="store_true", help="run in the background")
    job_opts(sp)
    sp.set_defaults(fn=cmd_topic)

    sp = sub.add_parser("games", help="the games a comparison can use (facts file and footage library of each)")
    sp.set_defaults(fn=cmd_games)

    sp = sub.add_parser("resume", help="continue a job, or redo it from a step")
    sp.add_argument("job")
    sp.add_argument("--from", dest="from_step", choices=STEP_NAMES, help="redo from this step (later steps are redone too)")
    job_opts(sp)
    sp.set_defaults(fn=cmd_resume)

    sp = sub.add_parser("status", help="steps of one job")
    sp.add_argument("job")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("list", help="recent jobs")
    sp.add_argument("--limit", type=int, default=30)
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("log", help="show a job's log")
    sp.add_argument("job")
    sp.add_argument("-n", "--lines", type=int, default=60)
    sp.add_argument("-f", "--follow", action="store_true")
    sp.set_defaults(fn=cmd_log)

    sp = sub.add_parser("delete", help="delete a job (database rows and files)")
    sp.add_argument("job")
    sp.add_argument("--keep-outputs", action="store_true")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("rewrite", help="regenerate one script section with an instruction")
    sp.add_argument("job")
    sp.add_argument("section", help="section id (hook, context, story, evidence, analysis, community, conclusion, cta ...)")
    sp.add_argument("instruction", help='e.g. "shorter, lead with the date"')
    sp.set_defaults(fn=cmd_rewrite)

    sp = sub.add_parser("voices", help="list available voices, or listen to samples (--sample)")
    sp.add_argument("voices", nargs="*", help="--sample: which voices (default: the best Kokoro voices, or your configured voice)")
    sp.add_argument("--provider", help="kokoro | elevenlabs | openai")
    sp.add_argument("--sample", action="store_true", help="read the same sentence with each voice into outputs/voice_samples/ to compare them")
    sp.set_defaults(fn=cmd_voices)

    sp = sub.add_parser("brand", help="the channel logo: install a new one (watermark of every video) and write the YouTube Studio images")
    sp.add_argument("image", nargs="?", help="a square image of the logo (PNG, JPG or WEBP); a logo on a black or white square is cut to its circle")
    sp.add_argument("--square", action="store_true", help="keep the whole square instead of cutting the circle")
    sp.set_defaults(fn=cmd_brand)

    sp = sub.add_parser("radar", help="GTA VI Content Radar: watch the sources, find new topics, propose videos")
    sp.add_argument("action", nargs="?", choices=["status", "scan", "tick", "worker", "list", "show", "set", "sources", "schedule"], default="status")
    sp.add_argument("args", nargs="*", help="show ID | set ID STATUS | sources [list|add TARGET|enable ID|disable ID|remove ID] | schedule [status|install|ensure|uninstall]")
    sp.add_argument("--trigger", default="cli", choices=["cli", "manual", "schedule"], help=argparse.SUPPRESS)
    sp.add_argument("--via", default="cron", help=argparse.SUPPRESS)
    sp.add_argument("--status", help="list: only this status (new, to_review, approved, rejected, published)")
    sp.add_argument("--all", action="store_true", help="list: include low-priority discoveries")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--kind", default="rss", help="sources add: rss | reddit | youtube | news_search")
    sp.add_argument("--name", default="", help="sources add: display name")
    sp.add_argument("--trust", default="", help="sources add: official | press | community | creator")
    sp.add_argument("--topic-only", action="store_true", help="sources add: every entry is about GTA VI")
    sp.set_defaults(fn=cmd_radar)

    sp = sub.add_parser("footage", help="real GTA 6 footage: add new footage, tag it, see what the narration will show")
    sp.add_argument("action", choices=("list", "update", "add", "tag", "autotag", "find", "concepts", "remove", "fetch", "stills"), nargs="?", default="list",
                    help="update: official trailers + any NEW official GTA VI video; add: files, folders or URLs; tag: set what a file shows; "
                         "autotag: let the local vision model tag everything; find: what a sentence would show; concepts: what the narration can call for")
    sp.add_argument("paths", nargs="*", help="add: files, folders or URLs; fetch/update: video URLs; tag/remove: a file name; find: a sentence")
    sp.add_argument("--tags", help="comma-separated tags, e.g. lucia,beach (characters, places, anything you will say in a video)")
    sp.add_argument("--at", help="tag: only this part of a video, START-END in seconds (e.g. 12-19.5)")
    sp.add_argument("--remove", help="tag: comma-separated tags to take off")
    sp.add_argument("--clear", action="store_true", help="tag: replace the existing tags instead of adding to them")
    sp.add_argument("--credit", help="add: on-screen credit (default: footage.credit)")
    sp.add_argument("--max-height", type=int, default=1080, help="fetch/update: max video height (default 1080)")
    sp.add_argument("--stills", type=int, default=12, help="stills per video pulled into photos/ (default 12)")
    sp.add_argument("--no-stills", action="store_true", help="do not pull stills from new videos")
    sp.add_argument("--no-new", action="store_true", help="update: do not look for new official videos")
    sp.add_argument("--no-autotag", action="store_true", help="do not run the vision tagger on new footage")
    sp.add_argument("--force", action="store_true", help="autotag: look again at footage already tagged")
    sp.add_argument("--limit", type=int, help="autotag: at most this many shots/photos")
    sp.add_argument("--model", help="autotag: Ollama vision model (default footage.vision_model)")
    sp.add_argument("--reindex", action="store_true", help="re-detect the shots of every video")
    sp.add_argument("-y", "--yes", action="store_true", help="remove: do not ask for confirmation")
    sp.add_argument("--game", help="another game's library, for comparisons: rdr2, gta5 ... (default: GTA 6)")
    sp.set_defaults(fn=cmd_footage)

    sp = sub.add_parser("competitors", help="refresh the competitor reference database from public channel metadata")
    sp.add_argument("channel_url", help="e.g. https://www.youtube.com/@SomeChannel")
    sp.add_argument("--refresh", action="store_true", help="ignore cached listings")
    sp.set_defaults(fn=cmd_competitors)

    sp = sub.add_parser("youtube", help="connect the YouTube channel (auth | whoami | logout | status JOB)")
    sp.add_argument("action", choices=("auth", "whoami", "logout", "status"))
    sp.add_argument("job", nargs="?")
    sp.add_argument("--no-browser", action="store_true", help="print the consent URL instead of opening the browser")
    sp.set_defaults(fn=cmd_youtube)

    sp = sub.add_parser("publish", help="upload a reviewed package to YouTube (private by default)")
    sp.add_argument("job")
    sp.add_argument("--title", type=int, help="pick title option N (1-3) from metadata.json")
    sp.add_argument("--title-text", help="your own title (the honesty rules still apply)")
    sp.add_argument("--thumbnail", type=int, default=1, help="thumbnail number in thumbnails/ (default 1)")
    sp.add_argument("--privacy", choices=("private", "unlisted", "public"), help="default: youtube.default_privacy (private)")
    sp.add_argument("--publish-at", help="schedule: 2026-11-20T18:00 (local time) or ISO with offset; uploads private until then")
    sp.add_argument("--override-qc", action="append", metavar="CHECK_ID", help="accept one failed QC check you verified yourself (repeatable)")
    sp.add_argument("--reviewed", action="store_true", help="confirm you did the REVIEW.md checklist (ticks the boxes)")
    sp.add_argument("--again", action="store_true", help="upload again even though it was already published")
    sp.add_argument("--dry-run", action="store_true", help="show exactly what would be sent, without uploading")
    sp.set_defaults(fn=cmd_publish)

    sp = sub.add_parser("dashboard", help="local web dashboard")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.set_defaults(fn=cmd_dashboard)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except FactoryError as exc:
        print(f"error: {exc.describe()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted - resume later with `python -m gta_factory resume <job>`", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
