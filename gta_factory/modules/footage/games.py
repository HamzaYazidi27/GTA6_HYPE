"""Other games next to GTA 6: one footage library per game, and the words that name each game.

Comparison videos ("GTA 6 vs Red Dead 2") show the other game's footage while the
narration talks about it. Each game of the ``games`` section of the config has its
own library folder (``footage_dir``, e.g. ``assets/game_footage/rdr2``) with exactly
the layout of the GTA 6 library (videos/, photos/, sidecars, index, auto tags).

Every function of ``library.py``, ``autotag.py`` and ``select.py`` works on any
library through :func:`game_settings`, a view of the settings in which the
``footage.*`` keys point at that game's folder, official sources and credit.
GTA 6 (``gta6``) is the default library, ``footage.dir``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import yaml

from ...app.core.errors import ConfigError
from .vocabulary import phrase_tokens, word_tokens

MAIN = "gta6"

# what is known about each game when the config does not say it
BUILTIN: dict[str, dict[str, Any]] = {
    "gta6": {"name": "Grand Theft Auto VI", "short": "GTA 6", "say": ["gta 6", "gta vi", "grand theft auto 6", "grand theft auto vi", "jason", "lucia", "leonida", "vice city"]},
}


def games(settings: Any) -> dict[str, dict[str, Any]]:
    """Every known game: {id: {"name", "short", "say", "footage_dir", "facts", ...}}."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    for gid, g in (settings.get("games") or {}).items():
        if isinstance(g, dict):
            out[str(gid)] = {**out.get(str(gid), {}), **g}
    for gid, g in out.items():
        g["id"] = gid
        g.setdefault("name", gid.upper())
        g.setdefault("short", g["name"])
        g["say"] = [str(x) for x in g.get("say") or []]
    return out


def resolve(settings: Any, name: str | None) -> str:
    """``rdr2``, ``RDR 2``, ``Red Dead`` or ``gta v`` -> the game id. Unknown names are an error that lists the games."""
    if not name:
        return MAIN
    known = games(settings)
    key = str(name).strip().lower()
    if key in known:
        return key
    toks = tuple(phrase_tokens(key))
    for gid, g in known.items():
        names = [gid, g["name"], g["short"], *g["say"]]
        if any(tuple(phrase_tokens(n)) == toks for n in names):
            return gid
    raise ConfigError(f"Unknown game '{name}'.", hint="Known games: " + ", ".join(f"{gid} ({g['name']})" for gid, g in known.items()) + ". Add one in the `games` section of config.yaml.")


def game(settings: Any, gid: str) -> dict[str, Any]:
    return games(settings)[resolve(settings, gid)]


class GameSettings:
    """The settings, seen from one game's footage library (``footage.*`` keys redirected)."""

    def __init__(self, base: Any, gid: str, overrides: dict[str, Any]):
        self._base = base
        self.game_id = gid
        self._over = overrides

    def get(self, dotted: str, default: Any = None) -> Any:
        if dotted in self._over:
            return self._over[dotted]
        if dotted == "footage":
            merged = dict(self._base.get("footage", {}) or {})
            merged.update({k.split(".", 1)[1]: v for k, v in self._over.items() if k.startswith("footage.")})
            return merged
        return self._base.get(dotted, default)

    def __getitem__(self, dotted: str) -> Any:
        sentinel = object()
        v = self.get(dotted, sentinel)
        if v is sentinel:
            raise KeyError(f"missing configuration key: {dotted}")
        return v

    def __getattr__(self, name: str) -> Any:  # root, jobs_dir, format(), video_profile() ...
        return getattr(self._base, name)


def game_settings(settings: Any, gid: str | None) -> Any:
    """``settings`` itself for GTA 6, else a view whose footage library is that game's."""
    gid = resolve(settings, gid)
    if gid == MAIN:
        return settings
    base = getattr(settings, "_base", settings)
    g = games(base)[gid]
    folder = g.get("footage_dir") or f"assets/game_footage/{gid}"
    over: dict[str, Any] = {
        "footage.dir": folder,
        "footage.sources": [str(u) for u in g.get("sources") or []],
        "footage.channels": [str(u) for u in g.get("channels") or ["https://www.youtube.com/@RockstarGames/videos"]],
        "footage.title_filter": str(g.get("title_filter") or re.escape(g["name"])),
        "footage.credit": str(g.get("credit") or base.get("footage.credit") or "Footage: Rockstar Games"),
        "footage.game_name": g["name"],
        "footage.extra_tags_files": [str(g["tags_file"])] if g.get("tags_file") else [],
    }
    return GameSettings(base, gid, over)


def footage_label(settings: Any) -> str:
    """'GTA 6' or the other game's short name, for log lines."""
    gid = getattr(settings, "game_id", MAIN)
    return game(settings, gid)["short"]


# ----------------------------------------------------------------------------- the library must not be empty

def library_state(settings: Any, gid: str) -> dict[str, Any]:
    from .library import build_index, library_summary

    return library_summary(build_index(game_settings(settings, gid)))


def ensure_footage(settings: Any, gid: str, *, log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Make sure a game's footage library has pictures: download its official videos when it is empty.

    A comparison is never rendered without the other game's footage: when nothing can be
    downloaded (no internet, an age-restricted video, a library you emptied), this raises
    with what to do instead of letting the video show placeholders.
    """
    from ...app.core.errors import FactoryError
    from . import library as lib

    g = game(settings, gid)
    gs = game_settings(settings, gid)
    summ = library_state(settings, gid)
    if summ["usable_shots"] or summ["photos"]:
        return summ
    log(f"footage: the {g['name']} library is empty - downloading its official videos now (one time) ...")
    try:
        lib.fetch(gs, None, log=log)
    except FactoryError as exc:
        log(f"footage: {exc.describe()}")
    try:
        lib.extract_stills(gs, log=log)
    except FactoryError as exc:
        log(f"footage: stills not extracted ({exc.message})")
    if settings.get("footage.auto_tag", True):  # so the narration can call for what is in each shot (cached, one time)
        try:
            from .autotag import autotag, vision_status

            if vision_status(gs)[0]:
                autotag(gs, log=log)
        except FactoryError as exc:
            log(f"footage: auto-tagging skipped ({exc.message})")
    summ = library_state(settings, gid)
    if not (summ["usable_shots"] or summ["photos"]):
        more = "" if gid == MAIN else f" --game {gid}"
        raise FactoryError(
            f"The {g['name']} footage library is empty and its official videos could not be downloaded, so this video would show no {g['short']} pictures.",
            hint=f"Run `python -m gta_factory footage update{more}` and read its error (if YouTube asks to sign in, set footage.cookies_from_browser: \"chrome\" in config.yaml),"
            f" or add your own {g['short']} clips: dashboard, page Footage, or `python -m gta_factory footage add <folder>{more}`. Then resume the job.",
        )
    return summ


# ----------------------------------------------------------------------------- facts about the other game

def facts_path(settings: Any, gid: str) -> Path:
    g = game(settings, gid)
    p = Path(str(g.get("facts") or f"knowledge/games/{g['id']}.yaml"))
    return p if p.is_absolute() else settings.root / p


def load_facts(settings: Any, gid: str) -> list[dict[str, Any]]:
    """The facts file of a released game: [{"id": "G1", "key", "about", "fact", "source"}] (ids are stable per file order)."""
    g = game(settings, gid)
    path = facts_path(settings, gid)
    if not path.exists():
        raise ConfigError(
            f"No facts file for {g['name']} ({path}).",
            hint=f"Create it (see knowledge/games/rdr2.yaml for the format): the comparison only says about {g['short']} what this file says.",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    source = str(data.get("source") or f"{g['name']} (the released game)")
    out = []
    for i, f in enumerate(data.get("facts") or [], 1):
        if isinstance(f, str):
            f = {"fact": f}
        if not isinstance(f, dict) or not str(f.get("fact") or "").strip():
            continue
        out.append({"id": f"G{len(out) + 1}", "key": str(f.get("id") or i), "about": str(f.get("about") or ""), "fact": re.sub(r"\s+", " ", str(f["fact"])).strip(), "source": str(f.get("source") or source)})
    if not out:
        raise ConfigError(f"{path.name} has no facts.", hint="Add facts under `facts:` (one short sentence each).")
    return out


# ----------------------------------------------------------------------------- which game a sentence talks about

def _phrases(names: list[str]) -> list[tuple[str, ...]]:
    return sorted({t for n in names if (t := tuple(phrase_tokens(n)))}, key=len, reverse=True)


def side_of(text: str, other: dict[str, Any], main: dict[str, Any]) -> str | None:
    """'other', 'gta6', 'both', or None when the sentence names neither game."""
    toks = [t for t, _ in word_tokens(text)]

    def names(phr: list[tuple[str, ...]]) -> bool:
        return any(tuple(toks[i : i + len(p)]) == p for p in phr for i in range(len(toks) - len(p) + 1))

    o = names(_phrases([other["name"], other["short"], other["id"], *other["say"]]))
    m = names(_phrases([main["name"], main["short"], *main["say"]]))
    if o and m:
        return "both"
    return "other" if o else "gta6" if m else None
