# Méthode

## `uuid` comme clé stable

Chaque ligne de l'index est indexée par l'`uuid` de la conversation. Les
titres changent, les projets changent, les exports sont régénérés —
l'uuid est la seule chose censée rester stable à travers tout cela, donc
chaque jointure de cet outil (export, correspondance, index précédent,
deltas) se fait sur lui.

## Index + deltas, pas une reconstruction complète à chaque fois

Reconstruire l'index depuis un export complet chaque jour ne passe pas à
l'échelle dès que vous avez des centaines de conversations et des
fichiers d'export volumineux. Le flux prévu est :

1. Demander un export **complet** de temps en temps (par exemple chaque
   mois) et construire un index frais à partir de lui, de votre fichier de
   correspondance et de votre index précédent.
2. Entre deux, appliquer de petits CSV **delta** (mêmes six colonnes que
   le fichier de correspondance : `uuid,title,created_at,updated_at,project_uuid,project_name`)
   dès que vous apprenez un changement (nouvelle conversation, conversation
   renommée, projet déplacé) sans tout réexporter.

## Union des lignes

Les lignes d'un index frais sont l'union de :

```
index précédent ∪ export ∪ correspondance ∪ deltas
```

Une conversation qui n'est plus couverte par l'export frais (le plus
souvent parce que vous avez utilisé un export limité dans le temps — voir
[export-format.md](export-format.md)) n'est **pas** supprimée. Ses champs
de contenu (`inferred_title`, `message_count`, `summary_excerpt`,
`first_message_excerpt`) sont repris tels quels depuis l'index précédent,
et `content_source` indique d'où ils viennent (`export`, `previous index`,
ou vide si aucune des deux sources n'avait rien). `source_line` indique de
même pourquoi la ligne existe, avec une valeur dédiée, `previous index
only`, pour une conversation trouvée dans aucune autre source.

## Règles de fraîcheur pour les deltas

Le `title`, `project_uuid` et `project_name` d'un delta ne remplacent la
valeur d'une ligne que si l'`updated_at` du delta (analysé en ISO-8601)
est **au moins aussi récent** que l'`updated_at` propre de la ligne. Un
delta sans `updated_at` n'est jamais appliqué à une ligne existante (seul
un uuid entièrement nouveau trouvé uniquement dans un delta est quand même
ajouté, avec `source_line = delta only`). Après une fusion réussie,
l'`updated_at` final de la ligne est le plus récent entre celui de la
ligne et celui du delta.

Séparément, le projet d'un delta n'est **jamais** appliqué à une
conversation présente dans le fichier de correspondance — la
correspondance est traitée comme un instantané complet et autoritaire,
donc un delta ne peut pas silencieusement réassigner le projet d'une
conversation. Cela ne concerne que `project_uuid`/`project_name` ; le
`title`, lui, peut toujours être mis à jour par un delta frais, que la
conversation soit ou non dans la correspondance.

Une particularité connue, héritée sans changement de la logique sur
laquelle cet outil est construit : `project_source` n'est pas recalculé
après la fusion d'un delta. Si un delta met à jour légitimement
`project_name`/`project_uuid` pour une conversation qui n'était *pas*
dans la correspondance, le `project_source` de la ligne affiche toujours
ce que l'étape de construction avait initialement mis (typiquement `not
in mapping`), même si les champs de projet eux-mêmes ont changé. Ce n'est
pas un bug à contourner dans votre propre outillage — juste un point à
connaître si vous lisez `project_source` comme une étiquette de
provenance.

## Jeux de tests

`tools/make_fixture.py` génère un jeu de données entièrement fictif
(`examples/`) avec des conversations « pièges » nommées qui exercent
chacune des règles ci-dessus : un delta plus ancien que la ligne qu'il
vise (ignoré), un delta plus récent (appliqué), une conversation
uniquement dans l'index précédent (conservée), une conversation sans
titre (titre déduit de son résumé), une conversation sans projet, une
conversation créée bien avant la fenêtre fictive de 30 jours mais mise à
jour à l'intérieur (présente dans l'export complet, absente de l'export
fenêtré), un uuid trouvé uniquement dans un delta, un delta sans
`updated_at` (ignoré), une conversation de la correspondance visée par un
delta plus frais qui tente de changer son projet (ignoré — la
correspondance gagne) et le cas miroir d'une conversation *absente* de la
correspondance visée par un delta plus frais (appliqué). Voir
`examples/TRAPS.json` pour les uuid exacts.
