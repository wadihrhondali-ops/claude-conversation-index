# claude-conversation-index

Construit et interroge un index CSV de vos conversations Claude.ai,
enrichi par votre propre correspondance conversation-projet. Bibliothèque
standard uniquement, aucune dépendance externe, aucun appel réseau.

([English](README.md))

## À quoi ça sert

L'export de données de Claude.ai ne dit pas de façon fiable à quel projet
appartient chaque conversation (voir [docs/export-format.md](docs/export-format.md)),
et un export limité dans le temps ne contient que les conversations
*créées* dans cette fenêtre, pas celles mises à jour. Cet outil combine
votre export, un fichier de correspondance que vous maintenez, votre
index précédent, et de petits fichiers « delta » incrémentaux en un seul
CSV que vous pouvez garder, comparer et interroger — sans jamais perdre
la trace d'une conversation simplement parce qu'un export ne la couvrait
pas. Voir [docs/method.fr.md](docs/method.fr.md) pour les règles complètes
(version anglaise : [docs/method.md](docs/method.md)).

Par défaut, aucun extrait de vos conversations n'est jamais copié dans
l'index — voir [docs/privacy.md](docs/privacy.md) (en anglais).

## Démarrage rapide (données fictives)

Tout ce qui suit s'exécute sur `examples/`, un jeu de données entièrement
synthétique — aucune conversation, aucun titre, aucun nom de projet réel.
Régénérez-le à tout moment avec `tools/make_fixture.py`.

```bash
git clone https://github.com/wadihrhondali-ops/claude-conversation-index.git && cd claude-conversation-index
python -I -B tools/make_fixture.py --out-dir examples   # (deja commite ; optionnel)

python -I -B src/claude_conversation_index/build_index.py \
    --export-json    examples/export-full/conversations.json \
    --mapping        examples/mapping.csv \
    --previous-index examples/previous-index.csv \
    --projects-dir   examples/export-full/projects \
    --export-date    2026-01-30 \
    --output-dir     /tmp/my-index \
    --deltas         "examples/*_delta_*.csv"

python -I -B src/claude_conversation_index/consult.py \
    --index  /tmp/my-index/claude-index_2026-01-30.csv \
    --export examples/export-full/conversations.json \
    --content "widget workshop"
```

C'est tout : `/tmp/my-index/` contient maintenant un index CSV, un rendu
Markdown, un CSV de comparaison avec l'index précédent et un CSV de
synthèse par projet, construits entièrement à partir de données fictives.

## Utilisation sur vos propres données

1. Demandez un export complet depuis Claude.ai : Réglages > Confidentialité
   > Exporter les données (voir [docs/export-format.md](docs/export-format.md)).
2. Produisez (par vos propres moyens — voir [docs/project-mapping.md](docs/project-mapping.md))
   un `mapping.csv` avec les colonnes `uuid,title,created_at,updated_at,project_uuid,project_name`.
3. Lancez `build_index` avec `--export-json`, `--mapping`,
   `--previous-index` (votre dernier index, ou un fichier vide avec juste
   l'en-tête la première fois), `--projects-dir` et `--export-date`.
4. Entre deux exports complets, consignez les petits changements dans un
   fichier daté `YYYY-MM-DD_delta_*.csv` (mêmes six colonnes) et passez-le
   via `--deltas`.
5. Recherchez avec `consult.py` — ajoutez `--content` pour aussi chercher
   dans le texte des messages (charge le ZIP complet ou
   `conversations.json` ; aucun réseau).

Installez-le proprement (optionnel) avec `pip install -e .` pour obtenir
les scripts `claude-index-build` / `claude-index-consult` et pouvoir
lancer `python -m claude_conversation_index.build_index` /
`...consult` ; sinon lancez simplement les deux scripts de
`src/claude_conversation_index/` directement, comme dans le démarrage
rapide ci-dessus.

## Limites

- Vous devez fournir `--mapping` vous-même ; cet outil ne récupère ni ne
  scrape votre liste de projets (voir [docs/project-mapping.md](docs/project-mapping.md)).
- Le comportement « `created_at` gouverne la fenêtre d'export, pas
  `updated_at` » (docs/export-format.md) a été observé une fois, sur un
  compte ; il ne se généralise peut-être pas à tous les comptes ou tous
  les exports.
- `project_source` n'est pas recalculé après la fusion d'un delta — voir
  [docs/method.fr.md](docs/method.fr.md).
- Aucun chiffrement, aucun stockage sécurisé : voir
  [docs/privacy.md](docs/privacy.md) (en anglais) pour ce qu'il ne faut
  jamais publier nulle part.

## Testé avec

Python 3.9.6 et 3.14.4, bibliothèque standard uniquement. Les tests utilisent
`pytest` (voir l'extra `dev` de `pyproject.toml`) ; rien d'autre n'est
requis pour lancer `build_index.py` / `consult.py` eux-mêmes.

## Licence

MIT — voir [LICENSE](LICENSE).
