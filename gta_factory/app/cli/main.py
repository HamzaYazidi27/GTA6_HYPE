"""Command line: ``python -m gta_factory <command>``.

    setup                      one-time install: Remotion packages, voice model, original audio library
    doctor                     check every tool and provider
    new URL [URL ...]          create a job from article URL(s) and run it
    resume JOB [--from STEP]   continue (or redo from a step)
    list | status JOB | log JOB | delete JOB
    rewrite JOB SECTION "..."  regenerate one script section, then resume from "scenes"
    voices                     list the voices of the TTS provider
    competitors CHANNEL_URL    refresh the competitor reference database
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
    if not args.skip_models and s.get("providers.tts.default") == "kokoro":
        ensure_kokoro(s, log=_print)
    lib = build_library(s.assets_dir, force=args.force_audio, log=_print)
    _print(f"audio library: {len(lib['generated'])} generated, {len(lib['kept'])} already present")
    (s.root / "knowledge").mkdir(exist_ok=True)
    if not (s.root / ".env").exists() and (s.root / ".env.example").exists():
        _print("tip: copy .env.example to .env only if you use API-key providers (the defaults need no key)")
    _print("setup complete. Next: `python -m gta_factory doctor`")
    return 0


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
    llm = get_llm(s)
    script = rewrite_section(llm, script=script, section_id=args.section, instruction=args.instruction, fact_check=fact_check, quotes=analysis.get("quotes") or [])
    write_json(root / "script" / "script.json", script)
    (root / "script" / "script.md").write_text(render_script_markdown(script, read_json(root / "story" / "story.json", {}), fact_check), encoding="utf-8")
    sec = next(x for x in script["sections"] if x["id"] == args.section)
    _print(f"[{args.section}] {sec['narration']}\n")
    _print(f"next: python -m gta_factory resume {args.job} --from scenes")
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    from ...providers.tts import get_tts

    tts = get_tts(get_settings(), args.provider)
    for v in tts.list_voices():
        _print(v)
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


def cmd_dashboard(args: argparse.Namespace) -> int:
    from ..dashboard.server import serve

    s = get_settings()
    serve(s, host=args.host or s.get("dashboard.host", "127.0.0.1"), port=args.port or int(s.get("dashboard.port", 8600)))
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    from ..core.pipeline import STEP_NAMES

    p = argparse.ArgumentParser(prog="python -m gta_factory", description="GTA 6 video factory: verified news videos from article URLs.")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("setup", help="one-time install (Remotion, voice model, audio library)")
    sp.add_argument("--skip-models", action="store_true", help="do not download the Kokoro voice model")
    sp.add_argument("--force-audio", action="store_true", help="regenerate the music beds and sound effects")
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
        sp.add_argument("--llm", help="LLM provider for this job (claude_code, anthropic, openai, ollama)")
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

    sp = sub.add_parser("voices", help="list available voices")
    sp.add_argument("--provider", help="kokoro | elevenlabs | openai")
    sp.set_defaults(fn=cmd_voices)

    sp = sub.add_parser("competitors", help="refresh the competitor reference database from public channel metadata")
    sp.add_argument("channel_url", help="e.g. https://www.youtube.com/@SomeChannel")
    sp.add_argument("--refresh", action="store_true", help="ignore cached listings")
    sp.set_defaults(fn=cmd_competitors)

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
