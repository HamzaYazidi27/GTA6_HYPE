"""Alerts for the discoveries that matter, without noise.

* one alert per opportunity, ever (a unique key in ``radar_notifications``);
* only for: a fresh official announcement, a new gameplay mechanic, a significant hidden
  detail, or an opportunity scoring at least ``radar.notifications.min_score``;
* at most ``radar.notifications.max_per_hour`` alerts per hour: the rest are grouped
  into one "N more opportunities" digest instead of one message each.

Every alert is shown in the dashboard. Telegram and e-mail are optional and use only
environment variables (``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_CHAT_ID``; ``RADAR_SMTP_HOST``,
``RADAR_SMTP_PORT``, ``RADAR_SMTP_USER``, ``RADAR_SMTP_PASSWORD``, ``RADAR_EMAIL_FROM``,
``RADAR_EMAIL_TO``). A failed delivery is recorded on the alert and never stops a scan.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Callable

import httpx

from .analyze import CATEGORY_LABEL
from .store import RadarStore, utcnow

KIND_TITLE = {
    "official": "Official GTA VI news",
    "new_mechanic": "New gameplay mechanic",
    "hidden_detail": "Hidden detail found",
    "high_opportunity": "High-potential video idea",
}


def alert_kind(opp: dict[str, Any], analysis: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """Which alert an analysed opportunity deserves, or None."""
    min_score = int(cfg.get("min_score", 75))
    s = int(opp.get("score") or 0)
    kind = (analysis.get("alert") or {}).get("kind")
    if kind == "official_announcement" and analysis.get("verification") == "official" and s >= int(cfg.get("min_score_official", 50)):
        return "official"
    if kind == "new_mechanic" and analysis.get("verification") in ("official", "credible", "community") and s >= int(cfg.get("min_score_mechanic", 60)):
        return "new_mechanic"
    if kind == "hidden_detail" and s >= int(cfg.get("min_score_detail", 60)):
        return "hidden_detail"
    if s >= min_score:
        return "high_opportunity"
    return None


def channels_ready() -> dict[str, bool]:
    return {
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")),
        "email": bool(os.environ.get("RADAR_SMTP_HOST") and os.environ.get("RADAR_EMAIL_TO")),
    }


def send_telegram(text: str, *, timeout: float = 15.0) -> str:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}, timeout=timeout)
        return "ok" if r.status_code == 200 else f"error HTTP {r.status_code}"
    except httpx.HTTPError as exc:
        return f"error {type(exc).__name__}"


def send_email(subject: str, body: str, *, timeout: float = 20.0) -> str:
    host = os.environ.get("RADAR_SMTP_HOST", "")
    port = int(os.environ.get("RADAR_SMTP_PORT") or 587)
    user, password = os.environ.get("RADAR_SMTP_USER", ""), os.environ.get("RADAR_SMTP_PASSWORD", "")
    msg = EmailMessage()
    msg["Subject"] = subject[:200]
    msg["From"] = os.environ.get("RADAR_EMAIL_FROM") or user or "radar@localhost"
    msg["To"] = os.environ.get("RADAR_EMAIL_TO", "")
    msg.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return "ok"
    except (smtplib.SMTPException, OSError) as exc:
        return f"error {type(exc).__name__}"


def _deliver(store: RadarStore, nid: int, title: str, body: str, *, external: bool) -> None:
    if not external:
        return
    ready = channels_ready()
    delivered: dict[str, str] = {}
    if ready["telegram"]:
        delivered["telegram"] = send_telegram(f"{title}\n\n{body}")
    if ready["email"]:
        delivered["email"] = send_email(f"[GTA VI Radar] {title}", body)
    if delivered:
        store.set_delivered(nid, delivered)


def notify_scan(store: RadarStore, settings: Any, analysed: list[dict[str, Any]], *, dashboard_url: str = "", log: Callable[[str], None] = lambda m: None) -> int:
    """Alerts for the opportunities analysed during a scan. Returns how many alerts were created."""
    cfg = settings.get("radar.notifications") or {}
    if not cfg.get("enabled", True):
        return 0
    per_hour = int(cfg.get("max_per_hour", 4))
    external_min = int(cfg.get("external_min_score", 80))
    already = store.notifications_since(utcnow() - timedelta(hours=1))
    candidates = []
    for opp in analysed:
        if opp.get("notified_at"):
            continue
        kind = alert_kind(opp, opp.get("analysis") or {}, cfg)
        if kind:
            candidates.append((kind, opp))
    candidates.sort(key=lambda kc: (kc[0] != "official", -int(kc[1].get("score") or 0)))
    created, overflow = 0, []
    for kind, opp in candidates:
        if already + created >= per_hour:
            overflow.append(opp)
            continue
        a = opp.get("analysis") or {}
        cats = ", ".join(CATEGORY_LABEL.get(c, c) for c in opp.get("categories") or [])
        title = f"{KIND_TITLE[kind]} · {opp['title'][:90]}"
        body = (
            f"Score {opp.get('score')}/100 · {str(opp.get('priority') or '').upper()} priority · {a.get('verification', '?')} · {opp.get('format') or '?'}\n"
            f"{cats}\n\n{a.get('what_is_new') or opp.get('summary') or ''}\n\n"
            f"Idea: {((a.get('ideas') or {}).get('titles') or [''])[0]}"
            + (f"\n\n{dashboard_url}" if dashboard_url else "")
        )
        nid = store.add_notification(opportunity_id=int(opp["id"]), kind=kind, title=title, body=body, dedupe_key=f"opp:{opp['id']}")
        store.update_opportunity(int(opp["id"]), notified_at=utcnow().isoformat(timespec="seconds"))
        if nid is None:
            continue
        created += 1
        _deliver(store, nid, title, body, external=kind == "official" or int(opp.get("score") or 0) >= external_min)
        log(f"radar: alert '{kind}' for #{opp['id']} {opp['title'][:70]}")
    if overflow:
        ids = ",".join(str(o["id"]) for o in overflow)
        title = f"{len(overflow)} more GTA VI video idea(s)"
        body = "\n".join(f"- {o['title'][:100]} (score {o.get('score')})" for o in overflow[:10])
        nid = store.add_notification(opportunity_id=None, kind="digest", title=title, body=body, dedupe_key=f"digest:{ids}")
        for o in overflow:
            store.update_opportunity(int(o["id"]), notified_at=utcnow().isoformat(timespec="seconds"))
        if nid is not None:
            created += 1
    return created
