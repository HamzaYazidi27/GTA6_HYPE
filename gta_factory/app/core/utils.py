"""Small shared helpers: ids, hashing, JSON io, text normalisation, subprocess."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from .errors import FactoryError

STOPWORDS = frozenset(
    """a an and are as at be been but by for from had has have he her his i if in into is it its of on or our
    she so that the their them then there these they this to was we were what when which who will with would
    you your not no can could should about after before over under more most than also just very new says said
    according report reports reported""".split()
)
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_|igshid|ncid|cmp|s_cid|ocid)", re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_job_id(slug_source: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = slugify(slug_source)[:28].strip("-")
    return f"{stamp}-{slug}" if slug else stamp


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def sha1(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha1(data).hexdigest()


def stable_hash(obj: Any) -> str:
    """Hash of any JSON-serialisable object, independent of key order."""
    return sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))[:16]


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def canonical_url(url: str) -> str:
    """Normalise a URL for de-duplication (drops tracking parameters and fragments)."""
    p = urlparse(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)])
    host = p.netloc.lower()
    path = re.sub(r"/+$", "", p.path) or "/"
    return urlunparse((p.scheme.lower() or "https", host, path, "", query, ""))


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9$%']+", (text or "").lower()) if t not in STOPWORDS and len(t) > 1]


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_for_match(text: str) -> str:
    """Lower-case, straighten quotes and collapse whitespace so quotes can be matched against a source."""
    t = (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.replace("—", "-").replace("–", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip().lower()


def contains_quote(haystack: str, quote: str, *, min_len: int = 12) -> bool:
    """True when ``quote`` appears verbatim (modulo typography/whitespace) in ``haystack``."""
    q = normalize_for_match(quote).strip('"\' .,')
    if len(q) < min_len:
        return False
    return q in normalize_for_match(haystack)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def which(binary: str) -> str | None:
    return shutil.which(binary)


def run_cmd(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
    input_text: str | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, raising a readable :class:`FactoryError` on failure."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None, input=input_text, env=env
        )
    except FileNotFoundError as exc:
        raise FactoryError(f"command not found: {cmd[0]}", hint=f"Install '{cmd[0]}' and make sure it is on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise FactoryError(f"command timed out after {timeout:.0f}s: {' '.join(cmd[:4])} ...") from exc
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        raise FactoryError(f"command failed ({proc.returncode}): {' '.join(cmd[:6])} ...\n" + "\n".join(tail))
    return proc
