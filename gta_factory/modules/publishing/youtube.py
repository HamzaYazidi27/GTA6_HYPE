"""YouTube publishing: OAuth for one channel, resumable upload, thumbnail, captions, playlist.

Nothing here runs on its own. Publishing is a separate, explicit command that only
accepts a package a human has reviewed:

* ``youtube.upload_enabled`` must be true in ``config.yaml``;
* the job must be ``completed`` (QC passed). A failed check can be overridden
  one by one with a recorded reason, except the checks that protect the facts,
  the rights and the file itself (:data:`NEVER_OVERRIDE`);
* every box of the "Avant de publier" checklist in ``REVIEW.md`` must be ticked;
* the video goes up **private** by default, or scheduled (private + ``publishAt``).

Standard library + httpx only: the OAuth "installed app" flow with a loopback
redirect and PKCE, and the documented REST endpoints of the YouTube Data API v3.
Every step writes ``jobs/<job>/publish/youtube.json`` as it goes, so an
interrupted upload resumes from the last byte YouTube acknowledged and a crash
after the upload never creates a second copy of the video.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ...app.core.errors import ConfigError, FactoryError, ProviderError
from ...app.core.utils import now_iso, read_json, write_json

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
# upload: videos.insert + thumbnails.set ; force-ssl: captions.insert, playlists, videos.list of private videos
SCOPES = ("https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl")

CHUNK = 8 * 1024 * 1024  # must be a multiple of 256 KiB
RETRY_STATUS = {500, 502, 503, 504}
MAX_RETRIES = 8
THUMB_MAX_BYTES = 2 * 1024 * 1024

# QC checks that protect the facts, the rights or the file itself: no override, ever.
NEVER_OVERRIDE = {
    "rights.cleared",
    "script.fact_discipline",
    "file.exists",
    "video.stream",
    "audio.stream",
    "captions.present",
    "format.short_limit",
    "package.title",
    "package.description",
}
PRIVACY = ("private", "unlisted", "public")
CHECKLIST_HEADING = "## Avant de publier"
BOX = re.compile(r"^(\s*-\s*\[)([ xX])(\]\s*)(.*)$")

Log = Callable[[str], None]


# =========================================================================== review checklist
def review_checklist(review_md: Path) -> list[dict[str, Any]]:
    """The boxes of the "Avant de publier" section of REVIEW.md."""
    if not review_md.exists():
        return []
    items, inside = [], False
    for line in review_md.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            inside = line.strip() == CHECKLIST_HEADING
            continue
        m = BOX.match(line) if inside else None
        if m:
            items.append({"text": m.group(4).strip(), "done": m.group(2) in "xX"})
    return items


def set_review_checklist(review_md: Path, done: list[bool] | bool) -> list[dict[str, Any]]:
    """Tick (or untick) the checklist boxes. ``done`` is one bool for all, or one per box."""
    if not review_md.exists():
        raise FactoryError("This job has no REVIEW.md yet.", hint="The checklist appears once the package step is done.")
    lines = review_md.read_text(encoding="utf-8").splitlines()
    inside, i = False, 0
    for n, line in enumerate(lines):
        if line.startswith("## "):
            inside = line.strip() == CHECKLIST_HEADING
            continue
        m = BOX.match(line) if inside else None
        if m:
            value = done if isinstance(done, bool) else (bool(done[i]) if i < len(done) else m.group(2) in "xX")
            lines[n] = f"{m.group(1)}{'x' if value else ' '}{m.group(3)}{m.group(4)}"
            i += 1
    review_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return review_checklist(review_md)


# =========================================================================== small helpers
def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_publish_at(value: str) -> str:
    """ISO date-time -> RFC 3339 UTC. A time without an offset is read as local time."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactoryError(f"Cannot read the date '{value}'.", hint="Use e.g. 2026-11-20T18:00 (local time) or 2026-11-20T17:00:00Z.") from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()  # local time zone of this machine
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise FactoryError(f"The scheduled time {dt.isoformat()} is in the past.")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _client_secrets_path(settings: Any) -> Path | None:
    raw = os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE") or str(settings.get("youtube.client_secrets_file") or "")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_absolute() else settings.root / p


def token_path(settings: Any) -> Path:
    p = Path(str(settings.get("youtube.token_file") or ".secrets/youtube_token.json")).expanduser()
    return p if p.is_absolute() else settings.root / p


def load_client(settings: Any) -> dict[str, str]:
    path = _client_secrets_path(settings)
    hint = (
        "Google Cloud Console -> APIs & Services: enable 'YouTube Data API v3', create an OAuth client ID of type "
        "'Desktop app', download its JSON and set YOUTUBE_CLIENT_SECRETS_FILE=/path/to/client_secret.json in .env."
    )
    if path is None:
        raise ConfigError("No OAuth client configured for YouTube.", hint=hint)
    if not path.exists():
        raise ConfigError(f"OAuth client file not found: {path}", hint=hint)
    data = json.loads(path.read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or data
    if not block.get("client_id") or not block.get("client_secret"):
        raise ConfigError(f"{path.name} has no client_id/client_secret.", hint=hint)
    return {"client_id": block["client_id"], "client_secret": block["client_secret"], "token_uri": block.get("token_uri") or TOKEN_URL, "auth_uri": block.get("auth_uri") or AUTH_URL}


def _save_token(path: Path, token: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    tmp.replace(path)


# =========================================================================== OAuth (installed app, loopback + PKCE)
class _CodeCatcher(BaseHTTPRequestHandler):
    result: dict[str, str] = {}

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        pass

    def do_GET(self) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if "code" not in q and "error" not in q:
            self.send_response(404)
            self.end_headers()
            return
        type(self).result.update(q)
        ok = "code" in q
        body = (
            "<!doctype html><meta charset=utf-8><title>NEON DISPATCH</title>"
            "<body style='background:#06070E;color:#F6F8FF;font:16px system-ui;display:grid;place-items:center;height:100vh;margin:0'>"
            f"<div><h1 style='color:{'#2EE59D' if ok else '#FF4D6D'}'>{'Channel connected' if ok else 'Authorization refused'}</h1>"
            "<p>You can close this tab and go back to the terminal.</p></div>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_auth_url(client: dict[str, str], redirect_uri: str, state: str, challenge: str) -> str:
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",  # always return a refresh token
        "include_granted_scopes": "true",
    }
    return f"{client['auth_uri']}?{urlencode(params)}"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize(settings: Any, *, open_browser: bool = True, timeout: float = 300, log: Log = print, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Run the consent flow once and store the refresh token. Returns the authorised channel."""
    client = load_client(settings)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    _CodeCatcher.result = {}
    server = HTTPServer(("127.0.0.1", 0), _CodeCatcher)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    url = build_auth_url(client, redirect_uri, state, challenge)
    log("Open this address, sign in with the Google account that owns the channel, and allow access:\n")
    log(url + "\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    try:
        while not _CodeCatcher.result and time.time() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
        server.server_close()
    res = dict(_CodeCatcher.result)
    if not res:
        raise FactoryError("No answer from the browser in time.", hint="Run `python -m gta_factory youtube auth` again.")
    if res.get("error"):
        raise FactoryError(f"Google refused the authorisation: {res['error']}.")
    if res.get("state") != state:
        raise FactoryError("The authorisation answer did not match this request (state mismatch).", hint="Run the command again.")
    token = exchange_code(settings, client, res["code"], redirect_uri, verifier, transport=transport)
    yt = YouTubeClient(settings, transport=transport)
    channel = yt.channel()
    token["channel"] = channel
    _save_token(token_path(settings), token)
    return channel


def exchange_code(settings: Any, client: dict[str, str], code: str, redirect_uri: str, verifier: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=30, transport=transport) as http:
        r = http.post(client["token_uri"], data={"code": code, "client_id": client["client_id"], "client_secret": client["client_secret"], "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier})
    if r.status_code != 200:
        raise ProviderError(f"Token exchange failed (HTTP {r.status_code}): {r.text[:300]}")
    data = r.json()
    if not data.get("refresh_token"):
        raise ProviderError("Google did not return a refresh token.", hint="Remove the app's access at myaccount.google.com/permissions, then run `youtube auth` again.")
    token = {"refresh_token": data["refresh_token"], "access_token": data.get("access_token"), "expires_at": time.time() + float(data.get("expires_in", 3600)) - 60, "scope": data.get("scope", " ".join(SCOPES)), "created_at": now_iso()}
    _save_token(token_path(settings), token)
    return token


def logout(settings: Any, *, transport: httpx.BaseTransport | None = None) -> bool:
    path = token_path(settings)
    token = read_json(path)
    if not token:
        return False
    try:
        with httpx.Client(timeout=15, transport=transport) as http:
            http.post(REVOKE_URL, params={"token": token.get("refresh_token", "")})
    except httpx.HTTPError:
        pass
    path.unlink(missing_ok=True)
    return True


# =========================================================================== API client
class YouTubeClient:
    def __init__(self, settings: Any, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.token_file = token_path(settings)
        self.http = httpx.Client(timeout=httpx.Timeout(60, connect=20), transport=transport, follow_redirects=False)
        self.sleep = sleep
        self._token: dict[str, Any] | None = None

    # ------------------------------------------------------------- auth
    @staticmethod
    def authorized(settings: Any) -> dict[str, Any] | None:
        tok = read_json(token_path(settings))
        return tok if tok and tok.get("refresh_token") else None

    def _load_token(self) -> dict[str, Any]:
        if self._token is None:
            tok = read_json(self.token_file)
            if not tok or not tok.get("refresh_token"):
                raise ConfigError("This machine is not connected to a YouTube channel yet.", hint="Run `python -m gta_factory youtube auth` once.")
            self._token = tok
        return self._token

    def access_token(self, *, force: bool = False) -> str:
        tok = self._load_token()
        if not force and tok.get("access_token") and float(tok.get("expires_at", 0)) > time.time():
            return str(tok["access_token"])
        client = load_client(self.settings)
        r = self.http.post(client["token_uri"], data={"client_id": client["client_id"], "client_secret": client["client_secret"], "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code in (400, 401):
            raise ConfigError(f"YouTube refused the saved authorisation ({r.json().get('error', r.status_code) if r.headers.get('content-type', '').startswith('application/json') else r.status_code}).", hint="Run `python -m gta_factory youtube auth` again.")
        if r.status_code != 200:
            raise ProviderError(f"Token refresh failed (HTTP {r.status_code}).")
        data = r.json()
        tok["access_token"] = data["access_token"]
        tok["expires_at"] = time.time() + float(data.get("expires_in", 3600)) - 60
        _save_token(self.token_file, tok)
        return str(tok["access_token"])

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}", **(extra or {})}

    # ------------------------------------------------------------- plumbing
    def request(self, method: str, url: str, *, retries: int = MAX_RETRIES, **kw: Any) -> httpx.Response:
        """Authenticated request with token refresh on 401 and backoff on 5xx / network errors."""
        headers = kw.pop("headers", {})
        refreshed = False
        for attempt in range(retries + 1):
            try:
                r = self.http.request(method, url, headers=self._headers(headers), **kw)
            except httpx.TransportError as exc:
                if attempt == retries:
                    raise ProviderError(f"Network error talking to YouTube: {exc}") from exc
                self.sleep(min(2**attempt, 60))
                continue
            if r.status_code == 401 and not refreshed:
                refreshed = True
                self.access_token(force=True)
                continue
            if r.status_code in RETRY_STATUS and attempt < retries:
                self.sleep(min(2**attempt, 60))
                continue
            return r
        raise ProviderError("YouTube kept failing.")  # pragma: no cover

    @staticmethod
    def _raise(r: httpx.Response, what: str) -> None:
        if r.status_code < 300:
            return
        reason, message = "", r.text[:300]
        try:
            err = r.json().get("error", {})
            message = err.get("message", message)
            reason = (err.get("errors") or [{}])[0].get("reason", "")
        except (ValueError, AttributeError):
            pass
        hint = {
            "quotaExceeded": "The project's daily YouTube API quota is used up; try again tomorrow (Pacific time reset).",
            "uploadLimitExceeded": "The channel reached its upload limit for now; try again later.",
            "forbidden": "Check that the signed-in account owns the channel.",
            "youtubeSignupRequired": "The Google account has no YouTube channel yet.",
        }.get(reason, "")
        raise ProviderError(f"{what} failed (HTTP {r.status_code}{', ' + reason if reason else ''}): {message}", hint=hint)

    def get_json(self, path: str, **params: Any) -> dict[str, Any]:
        r = self.request("GET", f"{API}/{path}", params=params)
        self._raise(r, f"GET {path}")
        return r.json()

    def post_json(self, path: str, body: dict[str, Any], **params: Any) -> dict[str, Any]:
        r = self.request("POST", f"{API}/{path}", params=params, json=body)
        self._raise(r, f"POST {path}")
        return r.json()

    # ------------------------------------------------------------- channel
    def channel(self) -> dict[str, Any]:
        items = self.get_json("channels", part="snippet,statistics", mine="true").get("items") or []
        if not items:
            raise ProviderError("The signed-in Google account has no YouTube channel.")
        c = items[0]
        return {"id": c["id"], "title": c["snippet"].get("title", ""), "custom_url": c["snippet"].get("customUrl", ""), "subscribers": (c.get("statistics") or {}).get("subscriberCount")}

    # ------------------------------------------------------------- resumable upload
    def start_upload(self, body: dict[str, Any], size: int, *, notify: bool) -> str:
        params = {"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "true" if notify else "false"}
        r = self.request("POST", f"{UPLOAD_API}/videos", params=params, json=body, headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"})
        self._raise(r, "videos.insert (start)")
        loc = r.headers.get("location")
        if not loc:
            raise ProviderError("YouTube did not return an upload session.")
        return loc

    def upload_offset(self, session_uri: str, size: int) -> int | dict[str, Any] | None:
        """Bytes YouTube already holds for this session, the finished video resource, or None if the session expired."""
        r = self.request("PUT", session_uri, headers={"Content-Range": f"bytes */{size}", "Content-Length": "0"})
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code == 308:
            return _range_end(r) + 1
        if r.status_code in (404, 410):
            return None
        self._raise(r, "upload status")
        return None  # pragma: no cover

    def send_file(self, session_uri: str, path: Path, size: int, offset: int, progress: Callable[[int], None]) -> dict[str, Any]:
        failures = 0  # consecutive chunks that did not move the upload forward
        with open(path, "rb") as f:
            while True:
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                try:
                    r: httpx.Response | None = self.http.put(session_uri, content=chunk, headers=self._headers({"Content-Range": f"bytes {offset}-{end}/{size}", "Content-Length": str(len(chunk))}))
                except httpx.TransportError:
                    r = None
                if r is not None and r.status_code in (200, 201):
                    progress(size)
                    return r.json()
                if r is not None and r.status_code == 308:
                    failures = 0
                    offset = _range_end(r) + 1
                    progress(offset)
                    continue
                if r is not None and r.status_code not in RETRY_STATUS and r.status_code != 401:
                    self._raise(r, "videos.insert (upload)")
                # network error, 5xx or expired access token: back off, ask YouTube where it stands, continue from there
                failures += 1
                if failures > MAX_RETRIES:
                    raise ProviderError("The upload keeps failing.", hint="Run the same publish command again: it resumes where YouTube stopped.")
                self.sleep(min(2 ** (failures - 1), 60))
                try:
                    if r is not None and r.status_code == 401:
                        self.access_token(force=True)
                    state = self.upload_offset(session_uri, size)
                except (ProviderError, httpx.TransportError):
                    continue  # retry the same chunk; counts as another failure if it fails again
                if state is None:
                    raise ProviderError("The upload session expired.", hint="Run the same publish command again: it starts a new session.")
                if isinstance(state, dict):
                    progress(size)
                    return state
                offset = int(state)
                progress(offset)

    # ------------------------------------------------------------- extras
    def set_thumbnail(self, video_id: str, image: Path) -> None:
        mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
        r = self.request("POST", f"{UPLOAD_API}/thumbnails/set", params={"videoId": video_id, "uploadType": "media"}, content=image.read_bytes(), headers={"Content-Type": mime})
        self._raise(r, "thumbnails.set")

    def insert_captions(self, video_id: str, srt: Path, *, language: str, name: str) -> str:
        boundary = "neon" + secrets.token_hex(12)
        meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n"
        ).encode() + srt.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        r = self.request("POST", f"{UPLOAD_API}/captions", params={"part": "snippet", "uploadType": "multipart"}, content=body, headers={"Content-Type": f"multipart/related; boundary={boundary}"})
        self._raise(r, "captions.insert")
        return str(r.json().get("id", ""))

    def find_or_create_playlist(self, title: str, *, description: str = "", privacy: str = "public") -> tuple[str, bool]:
        page = None
        while True:
            params: dict[str, Any] = {"part": "snippet", "mine": "true", "maxResults": 50}
            if page:
                params["pageToken"] = page
            data = self.get_json("playlists", **params)
            for p in data.get("items") or []:
                if p["snippet"].get("title", "").strip().lower() == title.strip().lower():
                    return p["id"], False
            page = data.get("nextPageToken")
            if not page:
                break
        created = self.post_json("playlists", {"snippet": {"title": title, "description": description}, "status": {"privacyStatus": privacy}}, part="snippet,status")
        return created["id"], True

    def add_to_playlist(self, playlist_id: str, video_id: str) -> None:
        self.post_json("playlistItems", {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}, part="snippet")

    def video_status(self, video_id: str) -> dict[str, Any] | None:
        items = self.get_json("videos", part="status,processingDetails,snippet", id=video_id).get("items") or []
        if not items:
            return None
        v = items[0]
        st, pd = v.get("status") or {}, v.get("processingDetails") or {}
        return {
            "upload_status": st.get("uploadStatus"),
            "privacy": st.get("privacyStatus"),
            "publish_at": st.get("publishAt"),
            "rejection_reason": st.get("rejectionReason"),
            "failure_reason": st.get("failureReason"),
            "processing": pd.get("processingStatus"),
            "title": (v.get("snippet") or {}).get("title"),
            "checked_at": now_iso(),
        }

    def close(self) -> None:
        self.http.close()


def _range_end(r: httpx.Response) -> int:
    m = re.match(r"bytes=0-(\d+)", r.headers.get("range", ""))
    return int(m.group(1)) if m else -1


# =========================================================================== the package and its gates
class Package:
    """What the pipeline left in outputs/<job>/ (the reviewed files are the ones uploaded)."""

    def __init__(self, settings: Any, job_id: str):
        self.settings = settings
        self.job_id = job_id
        self.dir = settings.outputs_dir / job_id
        self.publish_dir = settings.jobs_dir / job_id / "publish"
        self.record_path = self.publish_dir / "youtube.json"
        self.review = self.dir / "REVIEW.md"
        self.metadata: dict[str, Any] = read_json(self.dir / "metadata.json", {}) or {}
        self.qc: dict[str, Any] = read_json(self.dir / "qc_report.json", {}) or {}

    @property
    def video(self) -> Path | None:
        return next(iter(sorted(self.dir.glob("*.mp4"))), None) if self.dir.exists() else None

    @property
    def thumbnails(self) -> list[Path]:
        return sorted((self.dir / "thumbnails").glob("thumb_*.png")) if (self.dir / "thumbnails").exists() else []

    @property
    def captions(self) -> Path | None:
        p = self.dir / "captions.srt"
        return p if p.exists() else None

    def record(self) -> dict[str, Any]:
        return read_json(self.record_path, {}) or {}

    def save(self, record: dict[str, Any]) -> None:
        record["updated_at"] = now_iso()
        write_json(self.record_path, record)


def readiness(settings: Any, job: dict[str, Any] | None, pkg: Package, *, overrides: list[str] | None = None) -> dict[str, Any]:
    """Every reason the package cannot be published yet (``blockers``), plus warnings."""
    blockers: list[str] = []
    warnings: list[str] = []
    overrides = list(overrides or [])
    if not settings.get("youtube.upload_enabled"):
        blockers.append("Upload is off: set youtube.upload_enabled: true in config.yaml.")
    if job is None:
        blockers.append("Unknown job.")
    elif job["status"] == "running":
        blockers.append("The job is running; wait for it to finish.")
    elif job["status"] not in ("completed", "needs_review"):
        blockers.append(f"The job is '{job['status']}': only a finished package can be published.")
    failed = [c for c in pkg.qc.get("checks") or [] if not c["passed"] and c["severity"] == "critical"]
    for c in failed:
        if c["id"] in overrides and c["id"] not in NEVER_OVERRIDE:
            warnings.append(f"QC check {c['id']} failed and was overridden by the operator: {c['detail']}")
        elif c["id"] in NEVER_OVERRIDE:
            blockers.append(f"QC check {c['id']} failed and cannot be overridden: {c['detail']}")
        else:
            blockers.append(f"QC check {c['id']} failed: {c['detail']} (override with --override-qc {c['id']} if you checked it yourself)")
    if not pkg.qc:
        blockers.append("No QC report in the package.")
    if pkg.video is None:
        blockers.append("No video in the package.")
    if not pkg.metadata.get("title"):
        blockers.append("No metadata.json in the package.")
    if not pkg.thumbnails:
        warnings.append("No thumbnail: YouTube will pick a frame.")
    if pkg.captions is None:
        warnings.append("No captions.srt in the package.")
    items = review_checklist(pkg.review)
    if not items:
        blockers.append("REVIEW.md has no checklist.")
    elif not all(i["done"] for i in items):
        blockers.append(f"{sum(not i['done'] for i in items)} unchecked item(s) in the REVIEW.md checklist.")
    rec = pkg.record()
    if rec.get("state") == "done":
        blockers.append(f"Already published as {rec.get('url')} (use --again for a second upload).")
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings, "checklist": items, "upload_enabled": bool(settings.get("youtube.upload_enabled")), "authorized": YouTubeClient.authorized(settings) is not None}


def choose_title(pkg: Package, *, index: int | None = None, text: str | None = None, fact_check: list[dict[str, Any]] | None = None) -> str:
    from ..youtube_metadata.metadata import _title_ok

    if text:
        title = re.sub(r"\s+", " ", text).strip()
        why = _title_ok(title, fact_check or [])
        if why:
            raise FactoryError(f"Title refused ({why}): {title}", hint="The channel's honesty rules apply to custom titles too.")
        return title.replace("<", "‹").replace(">", "›")
    options = pkg.metadata.get("title_options") or [pkg.metadata.get("title")]
    if index is None:
        return str(pkg.metadata["title"])
    if not 1 <= index <= len(options):
        raise FactoryError(f"--title must be between 1 and {len(options)}.", hint="Options: " + " | ".join(f"{i}. {t}" for i, t in enumerate(options, 1)))
    return str(options[index - 1])


def build_request(settings: Any, pkg: Package, *, title: str, privacy: str, publish_at: str | None) -> dict[str, Any]:
    meta = pkg.metadata
    body = json.loads(json.dumps(meta.get("api_body") or {}))
    snippet = body.setdefault("snippet", {})
    snippet.update({"title": title[:100], "description": meta.get("description", ""), "tags": meta.get("tags") or [], "categoryId": str(meta.get("category_id") or settings.get("youtube.category_id", "20"))})
    lang = settings.get("channel.language", "en")
    snippet.setdefault("defaultLanguage", lang)
    snippet.setdefault("defaultAudioLanguage", lang)
    status = body.setdefault("status", {})
    status["privacyStatus"] = "private" if publish_at else privacy
    status["selfDeclaredMadeForKids"] = bool(meta.get("made_for_kids", settings.get("youtube.made_for_kids", False)))
    status["containsSyntheticMedia"] = bool(settings.get("youtube.contains_synthetic_media", False))
    status["embeddable"] = True
    status["license"] = "youtube"
    if publish_at:
        status["publishAt"] = publish_at
    else:
        status.pop("publishAt", None)
    return body


def _thumbnail_file(path: Path, workdir: Path) -> Path:
    """YouTube caps custom thumbnails at 2 MB: re-encode big PNGs as JPEG."""
    if path.stat().st_size <= THUMB_MAX_BYTES:
        return path
    out = workdir / (path.stem + ".jpg")
    for q in (2, 4, 6, 9):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-q:v", str(q), str(out)], check=True)
        if out.stat().st_size <= THUMB_MAX_BYTES:
            return out
    return out


class PublishLock:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "PublishLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(self.path.read_text().strip() or 0)
                os.kill(pid, 0)
                raise FactoryError(f"This job is already being published (process {pid}).")
            except (ValueError, ProcessLookupError):
                self.path.unlink(missing_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except PermissionError as exc:
                raise FactoryError("This job is already being published.") from exc
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.path.unlink(missing_ok=True)


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    """The record without the upload session URL (it works as a bearer credential for the upload)."""
    return {k: v for k, v in record.items() if k != "session_uri"}


# =========================================================================== publish
def publish(
    settings: Any,
    job_id: str,
    *,
    title_index: int | None = None,
    title_text: str | None = None,
    thumbnail: int = 1,
    privacy: str | None = None,
    publish_at: str | None = None,
    overrides: list[str] | None = None,
    again: bool = False,
    dry_run: bool = False,
    log: Log = print,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    from ...app.database.db import get_db

    db = get_db(settings.db_path)
    job = db.get_job(job_id)
    pkg = Package(settings, job_id)
    privacy = privacy or str(settings.get("youtube.default_privacy", "private"))
    if privacy not in PRIVACY:
        raise FactoryError(f"Unknown privacy '{privacy}'.", hint="private | unlisted | public")
    when = parse_publish_at(publish_at) if publish_at else None
    rec = pkg.record()
    if again and rec.get("state") == "done":
        archive = pkg.publish_dir / f"youtube.{rec.get('video_id', 'old')}.json"
        write_json(archive, rec)
        pkg.record_path.unlink()
        rec = {}

    resuming = bool(rec) and rec.get("state") not in (None, "done", "failed_before_upload")
    ready = readiness(settings, job, pkg, overrides=overrides)
    blockers = ready["blockers"]
    if blockers and not dry_run:  # a dry run previews the request and lists what still blocks
        raise FactoryError("Not publishable yet:\n  - " + "\n  - ".join(blockers), hint="Fix the points above (REVIEW.md, QC, config.yaml), then run publish again.")
    for w in ready["warnings"]:
        log(f"warning: {w}")

    fact_check = read_json(settings.jobs_dir / job_id / "research" / "fact_check.json", []) or []
    if resuming:
        title, privacy, when = rec["title"], rec["privacy"], rec.get("publish_at")
        thumb = Path(rec["thumbnail"]) if rec.get("thumbnail") else None
        log(f"resuming the publication started {rec.get('started_at')} ({rec.get('state')})")
    else:
        title = choose_title(pkg, index=title_index, text=title_text, fact_check=fact_check)
        thumbs = pkg.thumbnails
        if thumbs and not 1 <= thumbnail <= len(thumbs):
            raise FactoryError(f"--thumbnail must be between 1 and {len(thumbs)}.", hint="Files: " + ", ".join(p.name for p in thumbs))
        thumb = thumbs[thumbnail - 1] if thumbs else None
    body = build_request(settings, pkg, title=title, privacy=privacy, publish_at=when)
    video = pkg.video
    if video is None:
        raise FactoryError("No video in the package.", hint="Run the job to the end first.")
    size = video.stat().st_size
    plan = {
        "title": title,
        "privacy": body["status"]["privacyStatus"],
        "publish_at": when,
        "video": video.name,
        "size_mb": round(size / 1e6, 1),
        "thumbnail": thumb.name if thumb else None,
        "captions": bool(pkg.captions) and bool(settings.get("youtube.upload_captions", True)),
        "playlist": pkg.metadata.get("playlist") if settings.get("youtube.add_to_playlist", True) else None,
        "request": body,
    }
    if dry_run:
        return {"dry_run": True, **plan, "blockers": blockers, "warnings": ready["warnings"]}

    yt = YouTubeClient(settings, transport=transport, sleep=sleep)
    with PublishLock(pkg.publish_dir / ".lock"):
        try:
            channel = yt.channel()
            if not resuming:
                rec = {
                    "state": "starting",
                    "job_id": job_id,
                    "channel": channel,
                    "title": title,
                    "privacy": body["status"]["privacyStatus"],
                    "publish_at": when,
                    "thumbnail": str(thumb) if thumb else None,
                    "video_file": video.name,
                    "video_sha1": file_sha1(video),
                    "total_bytes": size,
                    "bytes_sent": 0,
                    "overrides": [w for w in ready["warnings"] if "overridden" in w],
                    "steps": {},
                    "started_at": now_iso(),
                }
                pkg.save(rec)
            elif rec.get("channel", {}).get("id") and rec["channel"]["id"] != channel["id"]:
                raise FactoryError(f"This upload was started on channel '{rec['channel'].get('title')}', but you are now signed in to '{channel['title']}'.", hint="Sign in to the original channel with `youtube auth`, or use --again.")
            elif rec.get("video_sha1") and rec["video_sha1"] != file_sha1(video):
                raise FactoryError("The video in the package changed since this upload started.", hint="Use --again to upload the new version.")
            log(f"channel: {channel['title']} ({channel['id']})")

            # 1) the video (resumable)
            if not rec.get("video_id"):
                _upload_video(yt, pkg, rec, body, video, size, log)
            # 2) thumbnail
            if thumb and rec["steps"].get("thumbnail") not in ("done",):
                rec["steps"]["thumbnail"] = _soft(lambda: yt.set_thumbnail(rec["video_id"], _thumbnail_file(thumb, pkg.publish_dir)), "thumbnail", log, hint_403="custom thumbnails need a verified channel (youtube.com/verify)")
                pkg.save(rec)
            # 3) captions
            if plan["captions"] and rec["steps"].get("captions") not in ("done",):
                lang = str(settings.get("channel.language", "en"))
                rec["steps"]["captions"] = _soft(lambda: yt.insert_captions(rec["video_id"], pkg.captions, language=lang, name=str(settings.get("youtube.captions_name") or "Subtitles")), "captions", log)
                pkg.save(rec)
            # 4) playlist
            if plan["playlist"] and rec["steps"].get("playlist") not in ("done",):
                def _pl() -> None:
                    pid, created = yt.find_or_create_playlist(plan["playlist"], description=f"{settings.get('channel.name', '')} - {settings.get('channel.tagline', '')}".strip(" -"))
                    yt.add_to_playlist(pid, rec["video_id"])
                    rec["playlist"] = {"id": pid, "title": plan["playlist"], "created": created}
                    log(f"playlist: {plan['playlist']}{' (created)' if created else ''}")

                rec["steps"]["playlist"] = _soft(_pl, "playlist", log)
                pkg.save(rec)
            rec["state"] = "done"
            rec["finished_at"] = now_iso()
            try:
                rec["youtube_status"] = yt.video_status(rec["video_id"])
            except FactoryError:
                pass
            pkg.save(rec)
        except FactoryError as exc:
            rec["state"] = "failed" if rec.get("video_id") or rec.get("session_uri") else "failed_before_upload"
            rec["error"] = exc.describe()
            if rec.get("job_id"):
                pkg.save(rec)
            raise
        finally:
            yt.close()

    summary = {"video_id": rec["video_id"], "url": rec["url"], "studio_url": rec["studio_url"], "privacy": rec["privacy"], "publish_at": rec.get("publish_at"), "uploaded_at": rec["finished_at"], "channel": rec["channel"]["title"]}
    db.merge_summary(job_id, youtube=summary)
    db.mark_story_published(job_id, rec["video_id"])
    if pkg.dir.exists():
        write_json(pkg.dir / "youtube.json", public_record(rec))
    return rec


def _upload_video(yt: YouTubeClient, pkg: Package, rec: dict[str, Any], body: dict[str, Any], video: Path, size: int, log: Log) -> None:
    offset: int | dict[str, Any] | None = None
    if rec.get("session_uri"):
        offset = yt.upload_offset(rec["session_uri"], size)
        if offset is None:
            log("the previous upload session expired: starting a new one")
            rec.pop("session_uri", None)
        elif isinstance(offset, int):
            log(f"resuming the upload at {offset / 1e6:.1f} / {size / 1e6:.1f} MB")
    if not rec.get("session_uri"):
        rec["session_uri"] = yt.start_upload(body, size, notify=bool(yt.settings.get("youtube.notify_subscribers", True)))
        rec["request"] = body
        offset = 0
    rec["state"] = "uploading"
    pkg.save(rec)
    last = {"t": 0.0, "pct": -1}

    def progress(sent: int) -> None:
        rec["bytes_sent"] = sent
        pct = int(sent * 100 / max(size, 1))
        if time.time() - last["t"] > 1.5 or sent >= size:
            pkg.save(rec)
            last["t"] = time.time()
        if pct // 10 != last["pct"] // 10:
            log(f"upload {pct}% ({sent / 1e6:.1f} / {size / 1e6:.1f} MB)")
            last["pct"] = pct

    resource = offset if isinstance(offset, dict) else yt.send_file(rec["session_uri"], video, size, int(offset or 0), progress)
    vid = resource.get("id")
    if not vid:
        raise ProviderError("YouTube finished the upload without returning a video id.")
    rec.update({"video_id": vid, "url": f"https://youtu.be/{vid}", "studio_url": f"https://studio.youtube.com/video/{vid}/edit", "state": "uploaded", "bytes_sent": size, "uploaded_at": now_iso()})
    rec.pop("session_uri", None)
    rec["steps"]["video"] = "done"
    pkg.save(rec)
    log(f"video uploaded: {rec['url']} ({rec['privacy']})")


def _soft(fn: Callable[[], Any], name: str, log: Log, *, hint_403: str = "") -> str:
    """Run an extra step; a failure is recorded and reported but never loses the uploaded video."""
    try:
        fn()
        log(f"{name}: done")
        return "done"
    except ProviderError as exc:
        msg = exc.message + (f" ({hint_403})" if hint_403 and "403" in exc.message else "")
        log(f"warning: {name} not set: {msg}")
        return f"failed: {msg}"
    except (OSError, subprocess.CalledProcessError) as exc:
        log(f"warning: {name} not set: {exc}")
        return f"failed: {exc}"


def refresh_status(settings: Any, job_id: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    pkg = Package(settings, job_id)
    rec = pkg.record()
    if not rec.get("video_id"):
        raise FactoryError("This job has not been uploaded to YouTube.")
    yt = YouTubeClient(settings, transport=transport)
    try:
        rec["youtube_status"] = yt.video_status(rec["video_id"]) or {"missing": True, "checked_at": now_iso()}
    finally:
        yt.close()
    pkg.save(rec)
    if pkg.dir.exists():
        write_json(pkg.dir / "youtube.json", public_record(rec))
    return rec

