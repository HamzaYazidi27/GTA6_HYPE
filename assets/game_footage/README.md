# Footage of the other games (comparisons)

Comparison videos (`python -m gta_factory topic "..." --vs rdr2`) show the other game while the
narration talks about it, and both games side by side on the split screens. Each game has its own
library here, with the same layout as `assets/gta6_footage` (`videos/`, `photos/`, tags):

```bash
python -m gta_factory footage update --game rdr2          # official Red Dead Redemption 2 trailers + gameplay video
python -m gta_factory footage update --game gta5          # official GTA V trailer + gameplay video
python -m gta_factory footage add ~/Captures/rdr2 --game rdr2 --tags horse,camp   # your own gameplay captures
python -m gta_factory footage list --game rdr2
```

Which game each folder belongs to, its official sources and the words that name it in the narration
are set in the `games` section of `config.yaml`. What the video may *say* about each game is in
`knowledge/games/<game>.yaml`.
