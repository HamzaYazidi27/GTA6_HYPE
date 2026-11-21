# GTA6_HYPE — usine à vidéos d'actualité GTA 6 vérifiées

Tu donnes l'URL d'un article sur GTA 6. L'usine produit un **paquet prêt à relire** : la vidéo MP4 (format long 16:9 ou Short 9:16), trois miniatures, les sous-titres, le titre, la description avec chapitres et sources, le script et un rapport de fact-check.

Chaîne : **Ghost Plates**, « Run the plates on GTA 6. » Le logo de la chaîne est le watermark de chaque vidéo (voir « Le logo de la chaîne »). Chaque affirmation à l'écran porte son statut : CONFIRMED, REPORTED, RUMOR ou SPECULATION.

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
- **Les vidéos avant les screenshots.** Un plan de gameplay ou de trailer qui montre ce qui est dit passe avant un screenshot, même un screenshot mieux tagué ; les screenshots ne servent qu'en complément (`photo_share: 0.08`, avant 0,25).
- **Ton gameplay** (le jeu est sorti) : `python -m gta_factory footage add ~/Captures/gta6 --own --tags gameplay` ajoute tes propres captures. `--own` garde le HUD du jeu (minimap, barre de vie), qui sinon ressemblerait à un logo incrusté. Ne l'utilise jamais pour la vidéo d'une autre chaîne. Les nouvelles vidéos officielles de Rockstar arrivent avec `python -m gta_factory footage update`.
- `match_spoken: true` : ce qui est dit est montré. `match_lead_seconds` : l'image arrive un peu avant le mot.
- `photo_share`, `photo_hold_seconds`, `max_clip_seconds` (6 s max par extrait), `auto_tag`, `vision_model`, `sources`, `channels`, `title_filter`.
- `footage.enabled: false` revient aux seules animations. Les miniatures utilisent aussi une vraie image du jeu, choisie d'après le titre (`footage.thumbnails`).
- Les plans sont détectés automatiquement ; les cartons noirs, le logo du début et l'écran de fin sont ignorés.
- Crédit « Footage: Rockstar Games » à l'écran et dans la description. Les fichiers contenant « leak », « leaked », « datamine » ou « reupload » ne sont jamais utilisés.
- **Pas de re-upload.** Trois contrôles, à l'indexation puis au moment du choix des plans :
  1. **D'où vient le fichier.** Une vidéo ou une image téléchargée depuis le post ou la chaîne de quelqu'un d'autre (X, TikTok, Instagram, une chaîne fan sur YouTube, un site d'actu) n'est **pas utilisée** : seules les vidéos de Rockstar, les sites officiels et tes propres captures (`--own`) passent. Pour une vidéo que tu as le droit d'utiliser : `footage add <url> --third-party-ok` (ou `"allow_third_party": true` dans le `.json` à côté).
  2. **Ce qu'il y a dans l'image.** Un logo, l'interface d'un post (« Follow », le nom du compte, le texte) ou des sous-titres qui restent au même endroit sont détectés, **sur toute la vidéo ou seulement sur une partie** : seuls les plans concernés sont écartés.
  3. **Le modèle de vision**, avant chaque vidéo, regarde les plans choisis qu'il n'a jamais vérifiés (watermark, post, sous-titres incrustés) ; s'il en trouve, les plans sont choisis à nouveau sans eux. Il faut qu'Ollama tourne ; `python -m gta_factory footage autotag` vérifie toute la bibliothèque d'un coup.
  Les trailers de Rockstar et tes captures `--own` ne sont pas concernés par le 2 (le HUD du jeu n'est pas un logo). Le `REVIEW.md` liste tout ce qui a été écarté.
- ⚠️ Les images appartiennent à Rockstar/Take-Two. Les chaînes d'actu les utilisent couramment, mais YouTube peut poser une **revendication Content ID** (monétisation reversée à Rockstar) ; c'est rare qu'une vidéo soit bloquée, mais c'est possible.

## Trouver la prochaine vidéo : `fresh`

```bash
python -m gta_factory fresh                  # les histoires GTA 6 des dernières 48 h, par catégorie
python -m gta_factory fresh -c map --hours 12
```

Catégories (section `fresh` de `config.yaml`, modifiables) : **news** (patchs, ventes, annonces), **secrets** (easter eggs, mystères), **map**, **missions** (guides), **heists** (braquages, argent). Sources lues en une fois, par RSS uniquement :

- les flux des médias (`sources.feeds` + `fresh.feeds` : IGN, GameSpot, VGC, Eurogamer, PC Gamer, Kotaku, GamesRadar+, Insider Gaming, Polygon, Push Square, Pure Xbox) ;
- Bing News et Google News pour les mots-clés de chaque catégorie ;
- **r/GTA6** (rising / new) : la communauté trouve les secrets, les coins de la carte et les gains des braquages en premier. Ce sont des **idées à vérifier dans ton jeu**, jamais une source citée ni des images utilisées.

Chaque histoire (les articles qui parlent de la même chose sont regroupés) reçoit un score de 0 à 100 : fraîcheur, fiabilité de la meilleure source (officielle > média fiable > le reste), nombre de médias qui en parlent, bruit dans la communauté, et **−40 si la chaîne a déjà fait la vidéo**. Pour chacune, la commande prête à lancer, avec les articles fiables en premier :

```
[ 63] How to Get All GTA 6 Ultimate Edition Bonuses: Claim Vintage City Rewards  (2 h)
      sources: Insider Gaming (secondary)
      python -m gta_factory new "https://insider-gaming.com/gta-6-ultimate-edition-vintage-city" --format guide
```

Le résultat complet est aussi enregistré dans `outputs/fresh/`.

## Les images et vidéos des articles

Ce que montre un article (le menu de sauvegarde, un coin de la carte, l'écran d'un braquage) est souvent exactement ce dont parle la vidéo. À l'étape `extract`, l'usine lit la page et garde :

- **l'image principale et les images du texte de l'article** (pas les « related stories », menus et avatars), dans leur plus grande taille ;
- **créditée à Rockstar** (« Image credit: Rockstar Games »), ou publiée par un **média fiable** sur GTA (« Image: GamesRadar+ ») ;
- **les vidéos YouTube de Rockstar** et **les posts des comptes Rockstar**.

Jamais : une **photo d'agence** (Getty, AP, Reuters… licenciée au média, pas à toi), une image qui n'est pas clairement sur GTA, le **post ou la chaîne de quelqu'un d'autre** (X, Instagram, TikTok, une chaîne fan), un site hors de la liste des médias fiables. `--third-party-media` les autorise pour un job si tu as le droit de les utiliser ; `--no-article-media` désactive tout.

Les fichiers vont dans `assets/gta6_footage/photos/articles/<job>/` (et `videos/articles/<job>/`) et ne servent **qu'à la vidéo de ce job** : chacun est montré sur la scène qui parle de ce qu'il montre (au moins deux mots en commun avec sa légende), et l'image principale sur une des premières scènes qui n'a rien de précis à montrer. Ils passent les mêmes contrôles que le reste du footage (logo ou post incrusté, et le modèle de vision écarte aussi une **photo réelle** qui n'est pas une image du jeu). Le `REVIEW.md` liste ce qui a été pris et ce qui a été laissé, avec la raison.

## Un seul sujet et du rythme (toutes les vidéos)

- **Un seul sujet.** L'analyse marque chaque affirmation et chaque citation : `core` (le sujet de l'article), `context` ou `off_topic` (une autre news dans la page : encadrés « related », promos, anniversaires…). Les `off_topic` sortent de la vidéo et sont listées dans le `REVIEW.md`. Le hook doit s'appuyer sur une affirmation `core` (sinon le script est réécrit, et le contrôle qualité `script.focus` bloque le statut `completed`).
- **Le titre est une promesse.** L'audit vérifie que le script donne ce que le titre annonce (`title_not_delivered`) et réécrit les phrases creuses (« this approach balances innovation with tradition »…), qui étaient ignorées avant.
- **Le rythme est réglé au même endroit pour toutes les vidéos** : section `rhythm` de `config.yaml`, profil `long` pour tout le 16:9 (news, breaking, recap, guide, analyse, details, comparaisons, top listes) et `short` pour tout le 9:16.

| Réglage (`rhythm.*`) | 16:9 (`long`) | 9:16 (`short`) | Avant |
|---|---|---|---|
| `target_scene_seconds` : une nouvelle image ou un nouveau graphique toutes les… | 6 s | 2,5 s | 8 s / 4 s |
| `max_scene_seconds` : une carte ou une image ne reste jamais plus de… | 9 s | 4,5 s | 14 s pour tout |
| `shot_hold_seconds` : un plan de gameplay / trailer dure au plus… | 4 s | 2,2 s | 6 s |
| `photo_hold_seconds` : un screenshot reste… | 3 s | 1,6 s | 3,5 s |
| `scene_gap_seconds` : respiration entre deux scènes | 0,18 s | 0,06 s | 0,28 s |
| `sentence_pause_seconds` : respiration entre deux phrases | 0,2 s | 0,08 s | 0,24 s |
| `tail_seconds` : silence après le dernier mot | 1 s | 0,4 s | 1,6 s |
| `outro_seconds` : écran de fin (les écrans de fin YouTube demandent 5 s) | 5,5 s | 2,5 s | 5,5 s |
| `voice_speed_factor` : Kokoro lit plus vite que `providers.tts.kokoro.speed` | +3 % (~150 mots/min) | +14 % (~170 mots/min) | — |
| `max_sentence_words` : une phrase plus longue est coupée en deux | 22 | 12 | — |
| `cta_max_words` : la question finale | 25 | 10 | — |
| `max_text_cards` : cartes de texte (fact-check, source, citation…) | sans limite | 2 | — |

- **Un Short dure ~35 s** (`formats.short`, avant 45 s) : hook d'une phrase de 10 mots maximum, puis des phrases de 6 à 12 mots. Pas de carte titre, deux cartes de texte au plus (les fact-checks et les citations d'abord) : le reste, ce sont de gros mots sur du gameplay. Aucune carte ne reste à l'écran après la voix.
- **Des hooks plus courts en 16:9** : environ 20 s (avant : 30 à 40 s du budget de mots), les mots gagnés vont à l'histoire.
- **Pas de « BREAKING » sur une vieille news ni sur des retours de joueurs** : le label n'est gardé que si la source a 2 jours ou moins et que l'affirmation est CONFIRMÉE ou RAPPORTÉE (pas une rumeur ni une spéculation). Si la source principale n'est pas un média fiable, le job et le `REVIEW.md` le signalent.
- **Texte à l'écran jamais coupé** : les gros mots sont remis sur 3 lignes au plus, sans « … », et une ligne ne finit jamais par « the », « of »… Le script est réécrit quand un même nom revient deux fois de suite dans une phrase (« According to X Staff, X Staff reports… »). Les lignes de la carte « What it means » doivent venir de la narration, sinon la scène redevient de la typographie.
- **Prononciation.** « GTA 6 » est lu « GTA Six » (et non « G T A Six », que la voix lisait « gee tee *uh* six »). Ajoutés : Strauss (Zelnick), DJ Khaled.

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

**Le footage de l'autre jeu** a sa propre bibliothèque (`assets/game_footage/<jeu>/`), avec les mêmes outils : `footage update|add|tag|autotag|list --game rdr2`. Tes propres captures de gameplay y ont leur place (`footage add ~/Captures/rdr2 --game rdr2 --tags horse,camp`). Si la bibliothèque est vide au lancement, le job **télécharge tout seul** les vidéos officielles du jeu (une fois) ; s'il n'y arrive pas, il **s'arrête dès le début** avec la marche à suivre, au lieu de faire une vidéo avec des trous. Une vidéo YouTube réservée aux adultes demande une connexion : connecte-toi à YouTube dans ton navigateur et mets `footage.cookies_from_browser: "chrome"` (ou `safari`, `firefox`…) dans `config.yaml`.

Les **bandes noires** des trailers au format cinéma sont détectées plan par plan et coupées : les images remplissent l'écran et les panneaux de l'écran partagé.

**Ajouter un jeu** (GTA IV, San Andreas…) : une entrée dans la section `games` de `config.yaml` (nom, mots qui le désignent dans la narration, dossier, trailers officiels), un fichier `knowledge/games/<id>.yaml` avec ses faits, et du footage. Réglages : section `topic` (nombre d'items, durée par item, sources lues).

## Script to Short : ton script → un Short fini

Tu colles ta narration, tu cliques **Générer** (carte **Script to Short** du dashboard), et l'usine livre un Short 9:16 prêt à publier où **chaque phrase passe sur les vraies images de GTA VI qui la montrent**, au moment exact où elle est dite.

```bash
python -m gta_factory short-from-script "GTA 5 never let you do any of these. Seven: go viral on in-game social media. ... Comment the number you're trying first."
python -m gta_factory short-from-script --file mon_script.txt --fps 60
```

Les étapes (reprenables une par une, comme les autres vidéos) :

| Étape | Ce qu'elle fait |
|---|---|
| `script_plan` | Découpe le script en **beats** (une idée visuelle, 1-3 s) sans changer un mot : accroche, numéros (7 → 1, « number one » annoncé puis révélé), items, comparaisons (« GTA 5 stopped at five » → images de GTA 5), appel à commenter. Pour chaque beat : ce qui doit être à l'écran, 1-3 descriptions d'image, les sosies à refuser (« un bateau à moteur » pour un jet ski). Modèle de `config.yaml`, ou règles si aucun modèle ne répond. |
| `claims` | Vérifie les affirmations du script dans les articles (recherche d'actu) ; sinon elles sont marquées **non vérifiées**. |
| `voice` | La voix (Kokoro par défaut), une prise par phrase. |
| `sync` | Alignement **mot par mot** sur l'audio (faster-whisper, `pip install -e ".[align]"`) : chaque coupe tombe 0,05 s avant le mot qu'elle illustre. |
| `footage` | Bibliothèque locale d'abord, puis nouvelles vidéos officielles Rockstar, puis l'index **CLIP** de toutes les images (2 par seconde, en cache). |
| `match` | Pour chaque beat : la fenêtre de gameplay qui montre **exactement** ça (vote CLIP contre les sosies, l'action visible dès la première image), vérifiée par le modèle de vision local (Ollama) quand il tourne. Une correspondance incertaine **n'est pas montrée** (`--allow-weak` pour l'autoriser) ; un beat que la bibliothèque ne montre pas est **cherché tout seul sur internet** (voir « Footage trouvé sur internet » plus bas). **L'écran n'est jamais vide** : si rien de vérifié n'est trouvé non plus, le beat montre **le footage le plus proche** de ce qui est dit (vidéos et images, celles du web comprises, jamais un logo ou un carton), signalé **« le plus proche (à remplacer) »** dans le rapport et le dashboard. Les clips montrés par les Shorts récents passent après les autres : pas les mêmes images d'une vidéo à l'autre. |
| `edit` | Timeline et style : clips coupés à l'image près, mouvement de caméra qui repart à chaque coupe (zoom avant et arrière en alternance), entrée de chaque numéro en zoom rapide avec flou de mouvement, punch-in + léger tremblement sur le numéro, étalonnage, badge « #n », sous-titres 1-3 mots, sons (riser vers le n°1, whoosh + impact sur chaque numéro, pop sur l'appel à commenter). Une image n'est jamais fixe : zoom doux avec glissement (Ken Burns). Un plan trop court pour sa phrase passe au ralenti (jusqu'à ×0,5) au lieu de figer la dernière image. EDL et manifeste des sources. |
| `render`, `mix` | Remotion 1080×1920 (60 i/s si le footage est en 60), H.264 / AAC, −14 LUFS. |
| `cover`, `short_metadata` | Couverture depuis la meilleure image vérifiée, titre, description (crédits du footage), hashtags. |
| `short_qc` | Contrôle complet : footage sur chaque beat, vérifié ; **synchro image par image dans le MP4 rendu** (l'image à l'écran au milieu de chaque coupe est comparée à l'image source choisie) ; coupes avec leurs mots ; uniquement du gameplay du bon jeu ; droits documentés ; sous-titres = script mot pour mot ; pas de blanc ; pas de clip répété ; affirmations. **« Entièrement vérifié »** seulement si tout passe. |
| `short_package` | `outputs/<job>/` : le MP4, `narration.wav`, `captions.srt`, `edit_decision_list.edl` (CMX 3600) + `.json`, `footage_manifest.json` (pour chaque coupe : phrase, temps, ce qui est montré, fichier, URL source, timestamp d'origine, début/fin du clip, confiance, statut de vérification, droits), `cover.png`, `title.txt`, `description.txt`, `hashtags.txt`, `production_report.md`, `sync_report.json`, `claims_report.md`, `rights_review.json`, `qc_report.json`. |

Dans le dashboard, la carte du job montre chaque beat avec son footage et son statut, le MP4, tous les fichiers à télécharger et le rapport ; **Reprendre** relance l'étape qui a échoué, **Refaire depuis** n'importe quelle étape (par ex. `footage` après avoir ajouté des vidéos).

**Footage trouvé sur internet** : pour chaque beat **MANQUANT** après la bibliothèque, l'usine cherche toute seule, sans que tu ajoutes rien :

1. **des vidéos YouTube** (« GTA 6 + les mots de la phrase + gameplay ») : celles de Rockstar d'abord, puis des vidéos de gameplay courtes dont le titre nomme le bon jeu. Les titres « fan made », concept, mods, réaction, leak, ou d'un autre jeu (une recherche GTA 6 qui ramène du GTA 5) sont écartés. Les meilleures (2 par beat, 720p, 10 min max) sont téléchargées dans `videos/web/<job>/` ;
2. **des images** (Bing Images, puis DuckDuckGo) dont le titre ou la page nomme le jeu, 640 px minimum, dans `photos/web/<job>/`.

Tout ce qui est trouvé passe **les mêmes contrôles** que la bibliothèque (logo / sous-titres incrustés, vote CLIP contre les sosies, modèle de vision) puis le beat est re-matché : une vidéo d'abord, une image (zoom lent) seulement si aucune vidéo ne montre la phrase. Un beat de comparaison (« GTA 5 stopped at five ») remplit aussi tout seul la bibliothèque GTA 5 avec ses vidéos officielles. **Ce qui est trouvé reste dans la bibliothèque** (`videos/web/`, `photos/web/`, avec pour quelle phrase il a été trouvé) : le Short suivant qui parle de la même chose le trouve sans chercher, et rien n'est téléchargé deux fois. Les autres vidéos (actu, comparaisons…) ne l'utilisent pas tant que `footage.use_web_found` est à `false` (droits non vérifiés) ; `script_short.web_footage.keep_in_library: false` le garde pour son seul job. Le rapport le marque **web** (« · web », « · photo » dans le dashboard) avec le lien de la source : le contrôle qualité passe avec un avertissement (« vérifie que tu peux l'utiliser avant de publier ») et la vidéo n'est pas « entièrement vérifiée » tant que tu ne l'as pas remplacé ou validé. Réglages : `script_short.web_footage` dans `config.yaml` ; `--no-web` (ou la case du dashboard) pour ne pas chercher, `--offline` pour ne rien télécharger du tout.

**Le footage** : la qualité du Short dépend de ta bibliothèque. Ajoute tes captures de gameplay (`footage add ~/Captures/gta6 --own`) ou les vidéos officielles (`footage update`) puis `python -m gta_factory footage index` (index CLIP, une fois ; le modèle ~150 Mo est téléchargé au premier usage, `pip install -e ".[clip]"`). Une vidéo d'un autre créateur n'est utilisée que si tu as le droit (`--third-party-ok`) ; sinon elle est signalée « droits non documentés » et bloque « entièrement vérifié ».

**Le style de montage** vient de `style/style_profile.json`. `python -m gta_factory style analyze https://www.youtube.com/@Tacticalpath --videos 8` télécharge les derniers Shorts de la chaîne de référence (pour analyse seulement, dans `assets/reference_videos/`, jamais utilisés dans une vidéo) et **mesure** : durée des plans et coupes/minute, zooms et punch-ins, flashs, position/taille/nombre de mots des sous-titres, débit de la voix, niveau de la musique sous la voix, sons sur les coupes, couleurs, longueur de l'accroche et de l'appel à commenter. Le profil (`status: measured`) et `style/style_reference_report.md` (URLs, temps des coupes, valeurs par vidéo) sont écrits ; l'éditeur en déduit ses réglages. Tant que l'analyse n'a pas tourné (ou si YouTube est inaccessible), le profil reste `not_measured` avec la raison, l'éditeur utilise le style par défaut de la chaîne, et chaque rapport le dit. On reproduit la grammaire de montage, jamais les vidéos, logos ou éléments de la chaîne de référence.

Réglages : section `script_short` de `config.yaml` (`fps`, `vision_verify`, `fetch_new_official`, `style_profile`, `never_blank` : `false` pour revenir à l'ancien comportement, les mots sur fond de la chaîne quand rien de vérifié ne montre la phrase).

## La voix

Dans le dashboard, carte **Voix de la narration** : choisis une voix et clique sur **▶** pour l'écouter lire la même phrase ; elle est utilisée pour les vidéos lancées ensuite. En ligne de commande : `python -m gta_factory voices --sample` (les meilleures voix Kokoro dans `outputs/voice_samples/`), puis `--tts` / `--voice` pour un job, ou `providers.tts` dans `config.yaml` pour toujours.

| Voix | Prix | Rendu |
|---|---|---|
| Kokoro (par défaut) | gratuit, local | correct mais reconnaissable comme synthétique ; `af_heart` (femme, US) est la plus naturelle, les voix d'homme le sont moins |
| OpenAI `gpt-4o-mini-tts` | ~0,015 $ par minute de voix (~0,15 $ pour 10 min) | voix US naturelles, guidées par `providers.tts.openai.instructions` (narrateur américain détendu) ; clé `OPENAI_API_KEY` dans `.env` |
| ElevenLabs | abonnement ElevenLabs | le plus humain ; clé `ELEVENLABS_API_KEY` dans `.env`, les voix de ton compte apparaissent dans la liste |

Les voix OpenAI et ElevenLabs lisent **toute la scène d'un coup** (l'intonation enchaîne d'une phrase à l'autre comme un vrai narrateur) ; Kokoro lit phrase par phrase avec une respiration naturelle entre deux (`audio.synthesis`, `rhythm.*.sentence_pause_seconds`).

## Le logo de la chaîne

Le logo rond de la chaîne (`assets/brand/logo.png`, réglé par `channel.logo` dans `config.yaml`) est :

- le **watermark** en haut à gauche de chaque vidéo (format long et Shorts), du début à la fin, comme le logo d'une chaîne de télé ;
- le grand logo de l'écran de fin ;
- le badge en haut à droite de chaque miniature.

Changer de logo : `python -m gta_factory brand mon_logo.png`. Une image carrée suffit : un logo rond livré sur un carré noir ou blanc est découpé à son cercle (`--square` garde tout le carré). La commande écrit aussi les deux images à mettre dans YouTube Studio, menu Personnalisation, onglet Branding :

- `assets/brand/youtube_profile_800.png` : la photo de profil (800 x 800) ;
- `assets/brand/youtube_watermark_150.png` : le filigrane vidéo (150 x 150, à afficher pendant toute la vidéo).

Réglages : `channel.watermark_opacity` (0.2 discret … 1.0 plein), et `channel.logo: ""` pour ne mettre aucun logo. Les vidéos déjà rendues gardent l'ancienne image : `python -m gta_factory resume <job> --from render` les met à jour.

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
