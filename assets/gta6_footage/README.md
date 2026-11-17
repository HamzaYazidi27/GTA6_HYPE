# Real GTA 6 footage

Fill this folder once, every new video uses it automatically (about 80 % of the running time, `footage.ratio`).

```bash
python -m gta_factory footage fetch            # official Rockstar trailers (footage.sources) + stills
python -m gta_factory footage add ~/Downloads/gta6_screens --tags lucia,beach   # your own official screenshots
python -m gta_factory footage list             # what is in the library
```

- `videos/` : trailers (mp4, mov, webm, mkv). Shots are detected automatically; dark title cards, logos and the end card are skipped.
- `photos/` : official screenshots (jpg, png, webp) and stills pulled from the trailers.
- Optional `<file>.json` next to a file: `{"tags": ["lucia", "car"], "credit": "Footage: Rockstar Games", "segments": [{"start": 12, "end": 19, "tags": ["police"]}], "exclude": [[0, 5]]}`

Tags (and words in file names) let a scene about Lucia pick a shot of Lucia. Files with "leak", "leaked", "datamine" or "reupload" in their name or tags are never used.

The files stay out of git (`.gitignore`).
