# GTA6_HYPE — usine à vidéos d'actualité GTA 6 vérifiées

Tu donnes l'URL d'un article sur GTA 6. L'usine produit un **paquet prêt à relire** : la vidéo MP4 (format long 16:9 ou Short 9:16), trois miniatures, les sous-titres, le titre, la description avec chapitres et sources, le script et un rapport de fact-check.

Chaîne : **NEON DISPATCH**, « GTA 6 news, verified. » Chaque affirmation à l'écran porte son statut : CONFIRMED, REPORTED, RUMOR ou SPECULATION.

Coût : **0 $, sans aucun abonnement ni clé API.** Tout tourne sur ta machine : le LLM est un modèle open-source servi par [Ollama](https://ollama.com) (gratuit), la voix (Kokoro) et l'alignement des sous-titres (Whisper) sont locaux, et la musique et les effets sont synthétisés par le projet. Un abonnement Claude ou une clé API restent possibles en option, jamais nécessaires.

## Installation et lancement : une seule commande

Dans le dossier du projet :

```bash
bash install.sh
```

Le script installe **tout ce qui manque** (Homebrew, ffmpeg, Node.js, Ollama, uv, Python 3.12 dans `.venv`, Remotion, les modèles d'IA, la voix, la musique, les vrais trailers de GTA 6), vérifie le tout (`doctor`) puis **ouvre le dashboard** dans le navigateur. Il peut être relancé à tout moment : il ne refait que ce qui manque ou est cassé. La première fois, compte 10 à 30 Go de téléchargements selon la RAM de ta machine (modèles d’IA et trailers).

Ensuite, pour lancer l'usine :

```bash
bash start.sh
```

ou **double-clic sur `GTA6 Factory.command`** (macOS ; la première fois : clic droit > Ouvrir). `start.sh` met le code à jour (`git pull`), installe ce qui manquerait, démarre Ollama et ouvre le dashboard (http://127.0.0.1:8600). Les étapes qui dépendent d'une installation incomplète se réparent aussi toutes seules pendant un job (musique, voix, Remotion).

### Installation à la main (si tu préfères)

Prérequis : macOS ou Linux, Python 3.11-3.13, Node 18+, ffmpeg et Ollama, tous gratuits :

```bash
brew install ffmpeg node ollama uv      # Linux : paquets ffmpeg/node + curl -fsSL https://ollama.com/install.sh | sh
```

```bash
cd ~/GTA6_HYPE
uv venv --python 3.12 .venv && uv pip install -e ".[align,footage,dev]"
source .venv/bin/activate
python -m gta_factory setup     # npm install, LLM local (Ollama), voix Kokoro (~350 Mo), musiques et effets
python -m gta_factory doctor --ping
python -m gta_factory footage update  # vrais trailers GTA 6 + nouveaux trailers (voir plus bas)
```

`setup` démarre Ollama et télécharge **une fois** le meilleur modèle pour ta RAM (`model: auto`), plus le petit modèle de vision qui tague le footage GTA 6 (`qwen2.5vl`, 3 à 6 Go) :

| RAM | Modèle | Taille |
|---|---|---|
| 48 Go et + | `qwen3:32b` | 20 Go |
| 32 Go | `qwen3:30b` | 19 Go |
| 16 Go | `qwen3:14b` | 9,3 Go |
| 8 Go | `qwen3:8b` | 5,2 Go |

Plus le modèle est gros, meilleurs sont le script et les titres. Pour en forcer un : `python -m gta_factory setup --model qwen3:8b` puis `providers.llm.ollama.model` dans `config.yaml`. Les garde-fous (preuves vérifiées mot pour mot, classement des affirmations par règles fixes, audit puis révision du script, contrôle qualité) ne dépendent pas du modèle : un modèle local ne peut pas faire passer une rumeur pour un fait.

## Vraies images de GTA 6 (80 à 90 % de la vidéo)

Les vidéos montrent les **vrais trailers, gameplay et screenshots officiels de GTA 6** sur **80 à 90 %** du temps (cible 85 %), et les animations de la chaîne sur le reste (graphiques, tableaux, sources). Les cartes fact-check, les chiffres et les sous-titres s'affichent par-dessus les images du jeu.

**Ce qui est dit est montré.** Quand la narration dit quelque chose que la bibliothèque a (« Lucia », « Vice City », « la police », « un alligator », « le deuxième trailer », ou n'importe quel tag que tu as mis sur un fichier), c'est **cette** image qui passe, coupée **au mot près** (grâce au timing mot à mot des sous-titres) et tenue tant qu'on en parle. Sinon, n'importe quelle image du jeu passe (les moins utilisées d'abord, pour ne pas se répéter).

```bash
pip install -e ".[footage]"                   # yt-dlp, une fois
python -m gta_factory footage update          # trailers officiels + TOUT nouveau trailer GTA VI de la chaîne Rockstar, photos tirées des vidéos, tags auto
python -m gta_factory footage list            # ce que contient la bibliothèque et ce que la narration peut appeler
```

### Ajouter du nouveau footage (quand tu veux, comme tu veux)

| Comment | Commande |
|---|---|
| Nouveau trailer officiel sorti | `python -m gta_factory footage update` (le trouve tout seul sur la chaîne Rockstar) |
| Une vidéo en ligne (YouTube, X…) ou une image | `python -m gta_factory footage add https://... --tags gameplay,jason` |
| Tes fichiers (vidéos, screenshots, un dossier entier) | `python -m gta_factory footage add ~/Downloads/gta6 --tags lucia,beach` |
| Glisser-déposer | dashboard → **Footage GTA 6** (envoi, URL, tags, aperçu, suppression) |
| À la main | copie les fichiers dans `assets/gta6_footage/videos/` ou `photos/` : la prochaine vidéo les utilise |

### Dire à l'usine ce que montre chaque fichier

- **Tags automatiques** : un modèle de vision **gratuit et local** (Ollama, `qwen2.5vl`, installé par `setup`) regarde chaque plan et chaque screenshot et note ce qu'il voit (Lucia, Jason, plage, police, bateau, alligator, Vice City…). Lancé tout seul après `update` / `add`, ou `python -m gta_factory footage autotag`. Résultats en cache : il ne regarde qu'une fois.
- **Tes tags** (toujours prioritaires) : `python -m gta_factory footage tag lucia_beach.jpg --tags lucia,beach`, ou une partie d'une vidéo : `footage tag "Trailer 2" --at 42-47.5 --tags jason,boat`. Le nom du fichier compte aussi (`lucia_beach_01.jpg` → lucia, beach ; « Trailer 3 » dans le nom → le troisième trailer).
- **Tester** : `python -m gta_factory footage find "Lucia échappe à la police à Port Gellhorn"` montre quelle image passerait pour chaque mot, et ce qui manque.
- **Nouveaux personnages, lieux, véhicules…** : ajoute-les dans `knowledge/footage_tags.yaml` (exemples dedans), ou mets simplement le tag sur un fichier : un screenshot tagué `jet ski` passe dès que la narration dit « jet ski ». Liste et couverture : `python -m gta_factory footage concepts`.
- Le `REVIEW.md` de chaque vidéo liste ce qui a été montré pour chaque mot, et ce qui a été dit sans image dans la bibliothèque (pour savoir quoi ajouter).

### Réglages (`config.yaml`, section `footage`)

- `ratio: 0.85`, `ratio_min: 0.8`, `ratio_max: 0.9` : part de la vidéo sur de vraies images. Le contrôle qualité vérifie la part réelle (`footage.share`).
- `match_spoken: true` : ce qui est dit est montré. `match_lead_seconds` : l'image arrive un peu avant le mot.
- `photo_share`, `photo_hold_seconds`, `max_clip_seconds` (6 s max par extrait), `auto_tag`, `vision_model`, `sources`, `channels`, `title_filter`.
- `footage.enabled: false` revient aux seules animations. Les miniatures utilisent aussi une vraie image du jeu, choisie d'après le titre (`footage.thumbnails`).
- Les plans sont détectés automatiquement ; les cartons noirs, le logo du début et l'écran de fin sont ignorés.
- Crédit « Footage: Rockstar Games » à l'écran et dans la description. Les fichiers contenant « leak », « leaked », « datamine » ou « reupload » ne sont jamais utilisés.
- ⚠️ Les images appartiennent à Rockstar/Take-Two. Les chaînes d'actu les utilisent couramment, mais YouTube peut poser une **revendication Content ID** (monétisation reversée à Rockstar) ; c'est rare qu'une vidéo soit bloquée, mais c'est possible.

## Vidéos « Details you missed »

Le format préféré des fans de GTA : la vidéo **s'arrête sur une image, zoome sur un petit détail, l'entoure d'un cercle néon et le nomme** (« #3 / 5 PANNEAU DE STATION-SERVICE »), avec le **timestamp** dans la vidéo d'origine pour que chacun puisse vérifier.

```bash
python -m gta_factory details "Trailer 2"                      # un Short vertical, 5 détails
python -m gta_factory details "Trailer 2" --format details -n 12   # une vidéo 16:9, 12 détails
python -m gta_factory details gameplay --scan-only             # juste chercher et lister les détails trouvés
python -m gta_factory details                                  # toute la bibliothèque
```

Ou dans le dashboard : page **Footage GTA 6** → « Vidéo Details you missed ».

1. **Recherche** : le modèle de vision local (le même que pour les tags) regarde chaque plan et pointe jusqu'à 3 petits détails par image (textes, panneaux, plaques, écrans, logos parodiques, animaux, personnages au fond…), avec leur position.
2. **Vérification** : chaque détail est recadré en gros plan et redonné au modèle, qui doit confirmer qu'il le voit (et relit le texte). Les détails non confirmés sont jetés.
3. **Choix** : les plus intéressants (un texte lisible compte plus), un par plan, espacés, jamais un détail déjà montré dans une vidéo précédente, dans l'ordre du trailer avec le meilleur pour la fin.
4. **Script** : le LLM local n'écrit que ce qui se voit ; toute interprétation est au conditionnel (« peut-être », « on dirait »), les mots « confirmed », « official », « leaked » sont refusés.
5. **Montage** : le plan joue ~1 s, se fige (flash), la caméra zoome sur le détail, le cercle se dessine, la carte s'affiche ; en 9:16 le cadrage suit le détail. Miniatures : gros plan du meilleur détail entouré.

La recherche est faite **une seule fois** par vidéo (cache `assets/gta6_footage/details.json`) ; la première fois sur un trailer peut prendre 20 à 60 min selon la machine. Le `REVIEW.md` liste chaque détail avec son timestamp : vérifie-les avant de publier, le modèle peut se tromper (surtout sur les petits textes). Un modèle de vision plus gros (`footage.vision_model: qwen2.5vl:32b`) trouve et lit mieux, si ta machine a la mémoire.

## Comparaisons et top listes

Deux formats très demandés, faits **item par item** à partir de plusieurs sources :

- **Comparaison** : « GTA 6 vs Red Dead 2 », « GTA 6 vs GTA 5 »… Un système par item (limite d'armes, wanted, focus, salle de sport…). Chaque item s'ouvre sur un **écran partagé** : l'autre jeu à gauche, GTA 6 à droite, le numéro et le titre au-dessus, « avant / maintenant » sous chaque côté. Ensuite, ce qui est dit sur l'autre jeu passe **sur ses images**, ce qui est dit sur GTA 6 sur celles de GTA 6, et une phrase qui parle des deux revient à l'écran partagé.
- **Top liste** : « 12 things to do on day one », « 10 features… ». Chaque entrée s'ouvre sur une carte numérotée (« #4 / 12 STEAL A CLASSIC CAR ») sur les images de GTA 6.

```bash
python -m gta_factory footage update --game rdr2      # une fois : trailers officiels de Red Dead 2 (ou --game gta5)

python -m gta_factory topic "Red Dead 2 systems inside GTA 6" --vs rdr2 --source https://www.ign.com/... --source https://...
python -m gta_factory topic "GTA 6 vs GTA 5: what changes" --vs gta5 -n 15
python -m gta_factory topic "12 things to do on day one in GTA 6" -n 12
python -m gta_factory topic "5 GTA 6 systems from Red Dead 2" --vs rdr2 --format short    # un Short vertical
python -m gta_factory games                            # les jeux comparables, leurs faits et leur footage
```

Ou dans le dashboard : carte **Comparaison ou top liste** (et, page **Footage GTA 6**, la carte « Footage des autres jeux »).

Les étapes : `research` (tes sources `--source` + les meilleurs articles trouvés par la recherche d'actu, sauf `--no-search`) → `analyze` (toutes les affirmations sur GTA 6 utiles au sujet, preuve mot pour mot) → `fact_check` (chaque source vérifie les autres, jamais elle-même) → `outline` (les items, dans l'ordre, chacun appuyé sur des affirmations vérifiées) → `script` (même discipline que les news : attribution, audit, révision) → `scenes`, puis les mêmes étapes de production que les autres vidéos. Les chapitres YouTube sont les titres des items, comme dans les vidéos de référence.

**Ce qui est dit sur l'autre jeu** vient uniquement de `knowledge/games/<jeu>.yaml` (le jeu sorti : Red Dead 2 et GTA 5 sont fournis, ~20 faits chacun). Un fait absent du fichier ne peut pas être dit : ajoute, corrige ou supprime des lignes librement, une phrase courte par fait. Ce qui est dit sur GTA 6 garde son statut à l'écran (CONFIRMED, REPORTED…), comme dans les news.

**Le footage de l'autre jeu** a sa propre bibliothèque (`assets/game_footage/<jeu>/`), avec les mêmes outils : `footage update|add|tag|autotag|list --game rdr2`. Tes propres captures de gameplay y ont leur place (`footage add ~/Captures/rdr2 --game rdr2 --tags horse,camp`). Une bibliothèque vide n'emprunte jamais les images de GTA 6 : ses scènes restent graphiques et le contrôle qualité le signale.

**Ajouter un jeu** (GTA IV, San Andreas…) : une entrée dans la section `games` de `config.yaml` (nom, mots qui le désignent dans la narration, dossier, trailers officiels), un fichier `knowledge/games/<id>.yaml` avec ses faits, et du footage. Réglages : section `topic` (nombre d'items, durée par item, sources lues).

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

Formats : `long` (~8 min), `breaking` (~4 min), `announcement`, `recap`, `analysis`, `guide` (~8-9 min), `short` (45 s). Et `details` (« details you missed », fait depuis le footage avec la commande `details`), `versus` et `toplist` (comparaisons et top listes, avec la commande `topic` ; leur durée suit le nombre d'items).

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

- Images du jeu : la bibliothèque `assets/gta6_footage` (trailers, gameplay et screenshots officiels, voir plus haut), en extraits courts, transformés par les graphiques et crédités. Pas de logos ni de vidéos d'autres créateurs. Les animations sont dessinées en code, et la musique et les effets sont synthétisés (aucun sample), donc la bande-son ne déclenche pas de Content ID.
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
