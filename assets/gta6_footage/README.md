# Real GTA 6 footage

Fill this folder once, every new video uses it automatically (80-90 % of the running time, `footage.ratio_min` / `ratio_max`).
What the narration says (Lucia, Vice City, the police, an alligator, the second trailer...) is shown at the moment it is said
when this library has it; otherwise any footage is shown.

```bash
python -m gta_factory footage update                       # official trailers + any NEW official GTA VI video, stills, auto tags
python -m gta_factory footage add <files, folder or URL> --tags lucia,beach
python -m gta_factory footage tag "Trailer 2" --at 42-47.5 --tags jason,boat
python -m gta_factory footage autotag                      # tag everything with the free local vision model (Ollama)
python -m gta_factory footage find "Lucia escapes the cops in Vice City"
python -m gta_factory footage list                         # what is in the library
```

Or the dashboard (`python -m gta_factory dashboard`, page "Footage GTA 6"): drag and drop files, paste a URL, edit tags, try a sentence.

- `videos/` : trailers and gameplay (mp4, mov, webm, mkv). Shots are detected automatically; dark title cards, logos and the end card are skipped.
- `photos/` : official screenshots (jpg, png, webp) and stills pulled from the videos.
- `autotags.json` : what the vision model saw in each shot / photo (your own tags always count more).
- Optional `<file>.json` next to a file (written by `footage tag` and the dashboard):
  `{"tags": ["lucia", "car"], "credit": "Footage: Rockstar Games", "segments": [{"start": 12, "end": 19, "tags": ["police"]}], "exclude": [[0, 5]]}`

New characters, places, vehicles: `knowledge/footage_tags.yaml`. Files with "leak", "leaked", "datamine" or "reupload" in their name or tags are never used.

The files stay out of git (`.gitignore`).
