"""GTA VI Content Radar: automated discovery of GTA VI topics worth a video.

Pipeline of one scan (``engine.run_scan``):

1. ``feeds``      read every due source (RSS/Atom, Reddit RSS, YouTube channel feeds,
                  Bing News RSS searches) with conditional requests and per-source back-off.
2. ``relevance``  keep entries about GTA VI, suggest categories, group the same story
                  told by several outlets into one *opportunity* (duplicates never reach the feed).
3. ``analyze``    first pass on all new headlines in one AI call (triage), then a full
                  analysis of the best ones: what is new, how solid it is, angle, format,
                  titles, hooks, a Shorts script or a long-video outline, with its sources.
4. ``notify``     alerts in the dashboard (and optionally Telegram / e-mail), one per opportunity.

``store`` keeps everything in the factory SQLite database (tables ``radar_*``),
``worker`` runs scans on schedule: inside the dashboard, as a standalone worker,
or from launchd / cron, so the radar keeps working when the dashboard is closed.
"""
