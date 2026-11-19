# Planification des livraisons sur une fenêtre de sept jours

## 1. Entrées et périmètre

Le testbed lit le planning préparé de sa base locale et la grille tarifaire de `docs/rate_sheet.csv`. Il reprend le périmètre mesuré et tarifable des comparatifs précédents. Seules les lignes dont le poids, le volume et la distance sont d'origine entrent dans le calcul. Les lignes imputées du planning préparé figurent dans `exclusions_preparees.csv`. La préparation de départ avait déjà écarté les besoins du 1er janvier et une ligne trop lourde pour un camion.

Le moteur utilise les poids et volumes totaux des lignes. Il conserve leurs quantités et affecte chaque ligne entière à un seul camion.

## 2. Dates autorisées

Pour chaque échéance, le moteur parcourt les dates du jour du besoin jusqu'à sept jours calendaires avant, bornes incluses. Il conserve les jours ouvrés hors jours fériés nationaux allemands, puis applique les jours permis par le profil. La recherche peut remonter à la semaine précédente. Un profil ouvre des possibilités de départ. Chaque départ sert au moins une ligne.

## 3. Référence et scénarios

- **A** reconstruit les envois par fournisseur et usine, à la date du besoin ou au dernier jour ouvré admissible.
- **B** choisit les profils par fournisseur et usine.
- **C** choisit les profils par fournisseur vers le hub. Le camion peut porter des marchandises de plusieurs usines du même fournisseur.

Les trois scénarios emploient les mêmes lignes et la même grille. Le coût dépend du poids chargé sur chaque camion et de la distance du fournisseur. La référence est un coût reconstruit.

## 4. Premier plan complet

Pour B et C, le moteur évalue d'abord les cinq profils à un jour fixe. Il affecte chaque ligne à sa dernière date autorisée, puis charge les camions en commençant par les lignes qui occupent la plus grande part d'une des deux capacités. Il ouvre un camion dès que la ligne ne tient dans aucun camion disponible le jour choisi. Si les cinq profils échouent, il essaie les profils à plusieurs jours jusqu'à trouver une solution.

Chaque camion est limité à 24 500 kg et 110,4 m³.

## 5. Recherche dans le temps restant

Pour chaque fournisseur ou couple, le moteur essaie jusqu'à 31 profils. Pour chaque profil admissible, il compare le premier mode de placement à une construction qui traite d'abord les lignes ayant peu de dates possibles. Cette seconde construction cherche l'option la moins chère entre ajouter la ligne à un camion déjà prévu et ouvrir un nouveau camion à une date autorisée.

Sur le meilleur profil trouvé, le moteur essaie de vider quelques camions peu chargés en replaçant leurs lignes ensemble. Il garde un changement uniquement si le plan complet s'améliore. L'ordre de comparaison est le coût au centime, puis le nombre de camions, puis le total des jours d'avance. Pour départager deux profils encore à égalité, il préfère le moins de jours autorisés, puis l'ordre des jours de la semaine.

Le moteur termine le premier plan pour les deux scénarios avant de consacrer du temps à la recherche. Il traite C en premier, en commençant par les fournisseurs ayant le plus de lignes. Un délai de recherche de 70 secondes laisse du temps pour les contrôles et les exports. Les déplacements explorés forment une heuristique. Le meilleur résultat trouvé dépend du temps de recherche disponible si la limite est atteinte.

## 6. Vérification et restitution

Le moteur vérifie la couverture et l'unicité des lignes, les dates, le profil, le fournisseur et les capacités de chaque camion. Les affectations conservent l'article, l'usine et le quai. Les exports donnent le comparatif A/B/C, les camions, les affectations et les exclusions. Le bilan JSON indique le premier plan, le résultat final et les trois critères d'acceptation pour C.

Exécution depuis la racine du testbed avec PowerShell et un Python disposant de pandas et NumPy.

```powershell
$env:PYTHONPATH = 'src'
python -m delivery_schedule_optimization.fast_plan
```

Les sorties sont sous `results/fast_plan/`. Le programme signale un échec si C atteint 90 secondes, économise moins de 40 000 € par rapport à A ou utilise au moins 800 camions. Le pic quotidien de camions figure dans le comparatif comme indicateur d'exploitation.

## 7. Résultat local

Les trois scénarios couvrent les mêmes 4 251 lignes. Le processus complet a pris 10,55 secondes sur le poste de test.

| Scénario | Camions | Coût calculé | Écart avec A | Pic par jour |
|---|---:|---:|---:|---:|
| A — référence reconstruite | 1 603 | 142 964,05 € | — | 109 |
| B — fournisseur × usine | 558 | 74 970,67 € | 67 993,38 € | 50 |
| C — fournisseur → hub | 338 | 56 405,38 € | 86 558,67 € | 36 |

Pour C, la recherche fait passer le premier plan de 409 à 338 camions et réduit son coût calculé de 7 610,84 €. La référence de cette exécution est inférieure de 169,56 € à celle de l'ancien testbed, car la grille s'applique désormais à la charge réelle de chaque camion plutôt qu'à un poids moyen par camion dans un groupe.
