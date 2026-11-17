# GTA6_HYPE — usine à vidéos d'actualité GTA 6 vérifiées

Tu donnes l'URL d'un article sur GTA 6. L'usine produit un **paquet prêt à relire** : la vidéo MP4 (format long 16:9 ou Short 9:16), trois miniatures, les sous-titres, le titre, la description avec chapitres et sources, le script et un rapport de fact-check.

Chaîne : **NEON DISPATCH**, « GTA 6 news, verified. » Chaque affirmation à l'écran porte son statut : CONFIRMED, REPORTED, RUMOR ou SPECULATION.

Coût : **0 $, sans aucun abonnement ni clé API.** Tout tourne sur ta machine : le LLM est un modèle open-source servi par [Ollama](https://ollama.com) (gratuit), la voix (Kokoro) et l'alignement des sous-titres (Whisper) sont locaux, et la musique et les effets sont synthétisés par le projet. Un abonnement Claude ou une clé API restent possibles en option, jamais nécessaires.

## Installation (une fois)

Prérequis : macOS ou Linux, Python 3.11+, Node 18+, ffmpeg et Ollama, tous gratuits :

```bash
brew install ffmpeg node ollama      # Linux : paquets ffmpeg/node + curl -fsSL https://ollama.com/install.sh | sh
```

```bash
cd ~/GTA6_HYPE
uv venv && uv pip install -e ".[align,footage,dev]"   # ou : python -m venv .venv && .venv/bin/pip install -e ".[align,footage,dev]"
source .venv/bin/activate
python -m gta_factory setup     # npm install, LLM local (Ollama), voix Kokoro (~350 Mo), musiques et effets
python -m gta_factory doctor --ping
python -m gta_factory footage fetch   # vrais trailers GTA 6 (voir plus bas)
```

`setup` démarre Ollama et télécharge **une fois** le meilleur modèle pour ta RAM (`model: auto`) :

| RAM | Modèle | Taille |
|---|---|---|
| 48 Go et + | `qwen3:32b` | 20 Go |
| 32 Go | `qwen3:30b` | 19 Go |
| 16 Go | `qwen3:14b` | 9,3 Go |
| 8 Go | `qwen3:8b` | 5,2 Go |

Plus le modèle est gros, meilleurs sont le script et les titres. Pour en forcer un : `python -m gta_factory setup --model qwen3:8b` puis `providers.llm.ollama.model` dans `config.yaml`. Les garde-fous (preuves vérifiées mot pour mot, classement des affirmations par règles fixes, audit puis révision du script, contrôle qualité) ne dépendent pas du modèle : un modèle local ne peut pas faire passer une rumeur pour un fait.

## Vraies images de GTA 6 (≈ 80 % de la vidéo)

Les vidéos mélangent les **vrais trailers et screenshots officiels de GTA 6** (environ 80 % du temps) et les animations de la chaîne (les ~20 % restants : graphiques, tableaux, sources). Les cartes fact-check, les chiffres et les sous-titres s'affichent par-dessus les images du jeu.

```bash
pip install -e ".[footage]"                   # yt-dlp, une fois
python -m gta_factory footage fetch           # télécharge les trailers officiels de Rockstar (footage.sources) + en tire des photos
python -m gta_factory footage add ~/Images/gta6 --tags lucia,beach   # tes propres screenshots officiels
python -m gta_factory footage list            # ce que contient la bibliothèque
```

- Les fichiers vont dans `assets/gta6_footage/videos/` et `photos/` (hors git). Tu peux aussi y copier les fichiers à la main.
- Les plans sont détectés automatiquement ; les cartons noirs, le logo du début et l'écran de fin sont ignorés. Chaque extrait dure au plus 6 s (`footage.max_clip_seconds`).
- Les tags (et les mots du nom de fichier) servent à choisir un plan qui correspond à la phrase : une scène qui parle de Lucia prend en priorité un fichier tagué `lucia`.
- Proportion réglable : `footage.ratio` (0.8 = 80 %), part de photos : `footage.photo_share`. `footage.enabled: false` revient aux seules animations.
- Les miniatures utilisent aussi une vraie image du jeu (`footage.thumbnails`).
- Crédit « Footage: Rockstar Games » à l'écran et dans la description. Les fichiers contenant « leak », « leaked », « datamine » ou « reupload » ne sont jamais utilisés.
- ⚠️ Les images appartiennent à Rockstar/Take-Two. Les chaînes d'actu les utilisent couramment, mais YouTube peut poser une **revendication Content ID** (monétisation reversée à Rockstar) ; c'est rare qu'une vidéo soit bloquée, mais c'est possible.

## Utilisation

```bash
# une vidéo longue à partir d'un ou plusieurs articles sur LA MÊME histoire
python -m gta_factory new https://www.ign.com/articles/... 

# un Short
python -m gta_factory new https://... --format short

# relire le script avant de lancer la voix et le rendu
python -m gta_factory new https://... --until script
python -m gta_factory rewrite <job> hook "plus court, commence par la date"
python -m gta_factory resume <job> --from scenes

# article payant ou bloqué : colle le texte dans un fichier
python -m gta_factory new https://site/article --text-file https://site/article=~/article.txt

python -m gta_factory list            # derniers jobs
python -m gta_factory status <job>    # étapes, erreurs, chemin du paquet
python -m gta_factory log <job> -f    # suivre le journal
python -m gta_factory dashboard       # interface web : http://127.0.0.1:8600
```

Formats : `long` (~8 min), `breaking` (~4 min), `announcement`, `recap`, `analysis`, `guide` (~8-9 min), `short` (45 s).

## Les 16 étapes

| # | Étape | Ce qu'elle fait |
|---|---|---|
| 1 | extract | Récupère et nettoie les articles (trafilatura, JSON-LD) |
| 2 | analyze | Extrait les affirmations, chaque preuve est vérifiée **mot pour mot** dans l'article |
| 3 | corroborate | Cherche d'autres sources (flux RSS des médias, Bing/Google News RSS) |
| 4 | fact_check | Classe chaque affirmation par **règles fixes** selon le niveau des sources (le LLM ne peut pas transformer une rumeur en fait) |
| 5 | select | **Score de nouveauté** (0-100), détection des doublons avec les vidéos passées, choix d'un angle original |
| 6 | script | Écriture pour l'oral + contrôle automatique + audit par un « rédacteur en chef » + une révision |
| 7 | scenes | Storyboard : 16 modèles animés (stat, timeline, carte fact-check, citation…), la narration ne peut pas être modifiée |
| 8 | rights | Validation des droits de chaque visuel et son |
| 9 | voice | Voix scène par scène (avec cache : modifier une scène ne regénère qu'elle) |
| 10 | timeline | Placement à l'image près, sous-titres mot à mot (Whisper), SRT/VTT |
| 11 | render | Rendu Remotion par morceaux (avec cache par morceau) |
| 12 | mix | Voix + musique avec ducking + effets, −14 LUFS, limiteur, encodage H.264/AAC |
| 13 | thumbnail | 3 miniatures (bandeau + tampon, chiffre géant, split), texte contrôlé |
| 14 | metadata | Titres, description (bilan confirmé/rapporté/rumeur, chapitres, sources, crédits), tags |
| 15 | qc | Contrôle du MP4 final : noir, image figée, silence, loudness, crêtes, synchro, sous-titres, script, droits |
| 16 | package | Copie dans `outputs/<job>/` + `REVIEW.md` (checklist avant publication) + mémoire des histoires |

Un job qui échoue garde tout ce qui a été fait. `resume <job>` reprend à l'étape en échec, `resume <job> --from <étape>` refait à partir d'une étape.

Si l'histoire a déjà été traitée ou si son score de nouveauté est trop bas, le job s'**arrête volontairement** (statut `stopped`). `resume <job> --force` produit quand même.

## Ce que contient `outputs/<job>/`

`<titre>.mp4`, `thumbnails/`, `captions.srt` / `.vtt`, `description.txt`, `metadata.json` (3 titres proposés, tags, chapitres, corps prêt pour l'API YouTube), `script.md`, `fact_check_report.md`, `sources.json`, `rights_review.json`, `qc_report.json`, et `REVIEW.md`.

Si un point **critique** du contrôle qualité échoue (droits non validés, affirmation non sourcée restée dans le script, crête audio, désynchro…), le job finit en `needs_review` au lieu de `completed`.

## Droits et règles éditoriales

- Images du jeu : uniquement les trailers et screenshots **officiels** de la bibliothèque `assets/gta6_footage` (voir plus haut), en extraits courts, transformés par les graphiques et crédités. Pas de logos ni de vidéos d'autres créateurs. Les animations sont dessinées en code, et la musique et les effets sont synthétisés (aucun sample), donc la bande-son ne déclenche pas de Content ID.
- Photos d'ambiance (optionnel) : Pexels/Pixabay si une clé est dans `.env`, ou tes propres images dans `assets/licensed_visuals/` **avec** un fichier `<image>.license.json` (exemple dans `gta_factory/modules/asset_management/rights.py`). Sans licence documentée, la scène redevient de la typographie animée.
- Mode `rights_review.mode: strict` par défaut : le matériel presse officiel est refusé. En mode `editorial`, il est accepté mais marqué « revue humaine ».
- Les mots « leak », « leaked », « datamine » et « reupload » bloquent un visuel.
- Le script n'utilise que les faits vérifiés. Les citations sont vérifiées mot pour mot, et « confirmed » ou « official » sont réservés aux affirmations confirmées.

## Configuration

Tout est dans `config.yaml` (valeurs par défaut : `gta_factory/app/config/defaults.yaml`). Les clés API vont dans `.env` (voir `.env.example`). Aucune n'est nécessaire avec la configuration par défaut.

- LLM : `providers.llm.default` = `ollama` (gratuit, 100 % local, par défaut). En option : `claude_code` (abonnement Claude), `anthropic` ou `openai` (clés API payantes). `think: true` laisse le modèle « réfléchir » avant de répondre (plus lent).
- Voix : `providers.tts.default` = `kokoro` (local), `elevenlabs` ou `openai`. Liste des voix : `python -m gta_factory voices`. Prononciations : `gta_factory/modules/voice_generation/pronunciation.yaml`.
- Musique : `audio.music_mood` = `pulse`, `tension` ou `drive`.
- Chronologie vérifiée : `knowledge/timeline.yaml`. Elle sert à repérer les vieilles infos ; l'usine y ajoute toute seule les dates confirmées.
- Upload YouTube : **désactivé** par défaut (`youtube.upload_enabled: false`), voir ci-dessous.

## Publier sur YouTube (optionnel, gratuit)

1. Google Cloud Console → active **YouTube Data API v3** → crée un identifiant OAuth de type **Application de bureau** → télécharge le JSON et mets `YOUTUBE_CLIENT_SECRETS_FILE=/chemin/client_secret.json` dans `.env`.
2. `youtube.upload_enabled: true` dans `config.yaml`, puis `python -m gta_factory youtube auth` (une fois : le navigateur s'ouvre, tu autorises ta chaîne ; le jeton reste dans `.secrets/`, jamais commité).
3. Relis le paquet et coche la checklist de `outputs/<job>/REVIEW.md` (ou confirme avec `--reviewed`).

```bash
python -m gta_factory publish <job> --dry-run                    # ce qui serait envoyé, sans rien envoyer
python -m gta_factory publish <job> --title 2 --thumbnail 1      # envoi en PRIVÉ + miniature + sous-titres + playlist
python -m gta_factory publish <job> --publish-at 2026-11-20T18:00   # programmé (heure locale)
python -m gta_factory youtube status <job>                       # traitement côté YouTube
```

Garde-fous : seul un job `completed` part (un échec critique du QC se lève check par check avec `--override-qc <id>`, sauf droits, fact-check, fichier et sous-titres, jamais), la checklist doit être cochée, le titre choisi repasse les règles d'honnêteté, et la vidéo est privée par défaut. L'upload est **reprenable** : relance la même commande après une coupure, elle reprend au dernier octet reçu par YouTube sans créer de doublon. Une miniature refusée (chaîne non vérifiée) n'annule pas l'upload. L'API ne permet pas d'épingler un commentaire : la commande t'affiche celui à épingler.

## Analyse des concurrents

`python -m gta_factory competitors https://www.youtube.com/@Chaine` met à jour `reference_analysis/competitor_video_database.json` (métadonnées publiques uniquement, aucun téléchargement de vidéo).

## Tests

```bash
python -m pytest -q                                  # rapide, hors ligne (LLM scripté, voix factice)
GTA_FACTORY_RENDER_TESTS=1 python -m pytest -q       # + rendu Remotion complet, mixage, QC et paquet (~1 min)
npm run typecheck                                    # templates Remotion
npm run studio                                       # aperçu des templates dans Remotion Studio
```
