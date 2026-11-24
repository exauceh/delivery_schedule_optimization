# Milestone 4 · Vérification du plan et des gains

## Notre méthode et son bilan

On choisit ensemble le profil de jours ouvrés, la date de départ et le camion de chaque ligne, par couple fournisseur × usine. Le moteur respecte la fenêtre J−7 à J ainsi que les limites de poids et de volume. Le coût dépend du tarif de chaque camion. On garde donc la possibilité d’ouvrir un camion de plus quand la grille rend cette option moins chère. Le solveur conserve le meilleur plan vérifié et une borne inférieure de coût pour mesurer la marge restante.

Le [bilan avant/après](bilan_avant_apres.ipynb) explique les cinq étapes, donne un exemple tarifaire et contrôle le plan à partir des [exports figés](resultats_verifies/). Il exécute les tests, vérifie les affectations et compare coûts, camions et remplissage par périmètre. La référence est reconstruite à partir des dates de besoin et de la grille tarifaire. Exécuter le notebook avec Python, pandas, NumPy, SciPy et holidays, avec le commit de données `a0e8aba` disponible dans Git.

## Vérification du plan de Wissam

Le [notebook du Milestone 4](milestone4_wissam.ipynb) utilise le plan retenu de Wissam au commit a0e8aba. Il rapproche d’abord les lignes et les camions, puis reconstruit le coût de référence et compare les coûts et le remplissage.

Depuis le dépôt, exécuter `git fetch origin` pour récupérer les objets Git de la branche de Wissam. Ouvrir le notebook avec un environnement qui contient pandas, NumPy, SciPy et holidays. Les résultats de la vérification sont enregistrés dans le notebook.

### Résultat du plan de Wissam

**760 camions, 66 180,66 € d’économies calculées et 46,11 s de bout en bout** sur le poste de test. Les affectations recalculées correspondent à l’export de Wissam. Le pic passe de 118 à 68 camions dans la journée la plus chargée et les pics baissent dans les quatre usines. [Détail des mesures](chrono_wissam.json)

La couverture s’applique aux 4 389 lignes préparées. L’audit relève 134 lignes dont une charge est imputée, 379 besoins du 1er janvier hors horizon et une ligne de 26,9 t écartée avant la planification. RG06 assouplie autorise les 142 affectations qui s’écartent de la lecture stricte.

Les 150 couples dont toutes les charges et distances sont d’origine représentent 55 097,58 € des économies calculées. Les 30 autres représentent 11 083,08 €. Cette ventilation décrit le plan publié, sans isoler l’effet de l’imputation.

Reproduire le scénario retenu seul depuis la racine du dépôt.

```powershell
python milestone4/chronometrer_wissam.py --workers 8
```

Le chronomètre inclut la lecture des objets Git locaux, la reconstruction de la référence, la génération des candidats, le lissage, les contrôles et les exports dans `results/milestone4-wissam/`.

## Contre-expertise et optimisation du coût

La comparaison garde le regroupement fournisseur × usine, RG05, RG06 assouplie et la fenêtre de sept jours. Le coût passe avant le nombre de camions. La durée globale de 90 secondes n’entre plus dans le choix.

| Périmètre | Lignes | Plan faisable | Camions | Borne inférieure | Écart restant | Couples certifiés |
|---|---:|---:|---:|---:|---:|---:|
| Avec imputation maximale | 4 389 | 110 060,05 € | 758 | 102 710,69 € | 7 349,36 € | 138 / 180 |
| Sans imputation, chiffrable | 4 251 | 76 536,50 € | 625 | 70 101,67 € | 6 434,84 € | 131 / 171 |

L’audit reprend les exports préparés avec la même exclusion de la ligne de 26,9 t. Dans le scénario sans imputation, les 134 charges inconnues et les 4 lignes dont la distance est inconnue restent hors du chiffrage. Les références reconstruites respectives valent 176 724,61 € et 142 964,05 €. Elles donnent 66 664,56 € et 66 427,55 € d’économies calculées. La différence entre les coûts ne mesure pas à elle seule l’effet d’une imputation.

1. Le programme choisit le jour de chaque ligne et les jours fixes du profil. Pour les journées à peu de lignes, il énumère tous les chargements possibles et résout le choix du jour et du camion ensemble.
2. Pour les gros couples, le premier passage relaxe le chargement individuel et calcule une borne. Les dates sont rechargées ensuite et comparées aux plans déjà faisables. Le mode `--joint` modélise chaque camion physique sur les couples sélectionnés.
3. Une borne indépendante empêche de confondre un résultat du solveur interrompu avec une preuve. Elle retire les contraintes de calendrier impossibles à comparer, puis exploite les blocs de besoins dont les fenêtres de sept jours ne se recouvrent pas. Les bornes sont combinées couple par couple.
4. Le dernier passage énumère les chargements des petits départs déjà datés. Il autorise une séparation de lignes lorsque la grille la rend moins chère, même si elles tiennent ensemble.

Retirer complètement la fenêtre donne un plancher de 70 001,03 € avec imputation et 36 333,78 € sans imputation chiffrable. Ces montants ne décrivent aucun plan réalisable. Le plan publié de Wissam peut déjà être battu sur le même jeu. L’optimalité globale des nouveaux plans reste à prouver.

```powershell
$env:PYTHONPATH = 'src'
python milestone4/optimisation_cout.py --wissam-only --bound-seconds 5
python milestone4/optimisation_cout.py --bound-seconds 6 --output results/optimisation-cout-v2
python milestone4/optimisation_cout.py --joint --couple SUP_00037:PLT_002 --couple SUP_00024:PLT_001 --workers 2 --bound-seconds 35 --output results/optimisation-cout-joint-pilote
python milestone4/borne_calendrier.py --output results/optimisation-cout
python milestone4/retenir_meilleurs_couts.py --run results/optimisation-cout --run results/optimisation-cout-v2 --run results/optimisation-cout-joint-pilote --floors results/optimisation-cout/bornes.csv
python milestone4/recharger_exact.py
```

Les limites des commandes s’appliquent à chaque résolution lourde de ce passage, sans imposer un délai global. Augmenter `--bound-seconds`, ou le mettre à zéro pour laisser le solveur chercher une preuve sur les couples ciblés. Un plan n’est certifié que si sa meilleure borne rejoint son coût faisable.

### Ouverture avant le 9 novembre

Le premier besoin est le 9 novembre. En conservant sept jours d’avance au maximum, la première date possible devient le 2 novembre. Les cinq jours ouvrés supplémentaires peuvent uniquement servir des besoins du 9 au 13 novembre. Le scénario maintient les jours fixes du profil et conserve l’exception de démarrage d’origine à partir du 9, afin de tester l’ouverture des dates sans changer cette règle.

```powershell
python milestone4/optimisation_cout.py --earliest 2026-11-02 --incumbent results/meilleurs-couts-affines/affectations.csv --output results/anticipation-02-nov
python milestone4/comparer_anticipation.py
```

Le comparatif distingue le meilleur coût observé après recalcul et un plan contrôlé. Ce dernier ne remplace un couple que si son nouveau plan coûte moins et utilise au moins une date avant le 9. La différence est un gain observé pour ces nouvelles affectations. Elle est strictement attribuable à la règle de date lorsque les deux coûts du couple sont certifiés.

| Périmètre | À partir du 9 | Plan contrôlé à partir du 2 | Gain certifié lié à l’ouverture | Camions avant / après |
|---|---:|---:|---:|---:|
| Avec imputation | 110 060,05 € | 109 998,71 € | 61,34 € | 758 / 756 |
| Sans imputation chiffrable | 76 536,50 € | 76 475,16 € | 61,34 € | 625 / 623 |

Les mêmes quatre couples sont certifiés avant et après. Ils utilisent cinq camions avant le 9 pour 18 lignes, dont une le 3 novembre et 17 le 6. Le meilleur run avec imputation descend aussi à 109 971,44 €, avec 27,27 € d’amélioration supplémentaire sur des couples où la nouvelle option de date n’est pas utilisée pour améliorer le coût.

### Vérification par partitionnement exact des chargements

Le testbed reprend les données préparées du commit `a0e8aba`. Il travaille par couple fournisseur × usine et compare chaque date d’ouverture sur un périmètre identique. Les charges sont les totaux de ligne. Une ligne reste entière, les capacités s’appliquent en poids et en volume, et la grille tarifaire évalue chaque camion individuellement.

1. Pour chacun des 31 profils de jours ouvrés, on énumère les chargements compatibles avec la fenêtre de J−7 à J et les deux capacités. Le solveur affecte chaque ligne exactement une fois et minimise le coût total.
2. La première formulation crée une variable par chargement et date admissible. Elle impose au moins une semaine où chaque jour nominal du profil est couvert sous l’interprétation retenue de RG05, avec RG06 assouplie et l’exception de démarrage maintenue.
3. La seconde crée une variable par chargement et conserve ses dates possibles. Le théorème de Hall impose que tout ensemble de jours de la semaine témoin ait accès à au moins autant de camions distincts que de jours. Un appariement reconstruit les dates et le programme contrôle chaque affectation. Si deux jours nominaux se rabattent sur la même date à cause d’un férié, cette convention exige toujours deux camions distincts.
4. Les deux formulations doivent prouver le même optimum de coût. Les camions affichés décrivent les plans retournés. Leur nombre peut varier entre solutions de même coût et ne fait pas l’objet d’une preuve de minimum secondaire.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
python -m unittest discover -s milestone4 -p test_partition_hall.py -v
python milestone4/pilote_partition_hall.py --output results/pilote-hall
python milestone4/pilote_partition_hall.py --couple SUP_00148:PLT_001 --output results/pilote-hall-supplement
python milestone4/pilote_partition_hall.py --all-safe --max-items 8 --workers 5 --output results/hall-petits-couples
```

Chaque exécution exporte le comparatif, les affectations obtenues avec Hall et les couples au-delà du seuil d’énumération. Le seuil porte sur le nombre de lignes potentiellement éligibles le même jour. Il limite le champ de la preuve, car le nombre de chargements possibles croît exponentiellement. L’imputation reste une hypothèse du scénario préparé. Les preuves de coût s’appliquent aux entrées et aux règles modélisées.

Les six tests de régression passent. Les deux formulations concordent sur les 20 résolutions du pilote, qui croise cinq couples, deux périmètres et deux dates d’ouverture. Les coûts sont calculés au tarif du jeu préparé puis arrondis pour l’affichage.

| Périmètre sur les cinq couples | Lignes | Optimum à partir du 9 | Optimum à partir du 2 | Gain dû à l’ouverture | Camions avant / après |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 99 | 6 429,93 € | 6 368,60 € | 61,34 € | 40 / 38 |
| Sans imputation, chiffrable | 83 | 3 007,29 € | 2 945,95 € | 61,34 € | 24 / 22 |

Le gain est calculé avant arrondi. Quatre couples profitent de l’ouverture. Le cinquième, `SUP_00001 × PLT_004`, contient 16 lignes imputées dans le scénario préparé et ne gagne rien avec l’ouverture. Les deux périmètres ont des lignes différentes. Leurs coûts totaux ne permettent pas d’isoler l’effet économique de l’imputation.

Au 2 novembre, le lot limité à huit lignes éligibles par jour certifie 119 couples avec imputation (824 lignes) et 113 sans imputation chiffrable (790 lignes). Le pilote ajoute un couple avec imputation à cette date. La couverture cumulée de cette méthode est de 120 sur 180 et 113 sur 171, respectivement, sans additionner les couples vérifiés dans les deux périmètres. Pour les 232 résolutions comparables du lot, les durées cumulées par couple atteignent 594 s avec dates explicites et 988 s avec Hall. Ce sont des sommes de durées relevées sur des exécutions parallèles. L’énumération seule ne fournit pas de preuve globale au-delà de cette couverture.

### Résolution ciblée des couples encore ouverts

Les essais ciblent 20 couples distincts. Le modèle détaillé place les lignes dans des camions individuels. Le modèle agrégé renforce la borne avec la charge maximale éligible par jour, les tranches qu’imposent les lignes les plus lourdes et les incompatibilités au-delà de la moitié d’une capacité. Pour chaque couple, on retient le plan faisable le moins cher et la borne inférieure la plus forte. La consolidation contrôle la couverture exacte des lignes, les deux capacités, les jours ouvrés, J−7 à J et la semaine témoin du profil.

| Périmètre | Coût du plan avant | Meilleur coût faisable | Camions | Borne inférieure | Écart restant | Couples certifiés avant → après |
|---|---:|---:|---:|---:|---:|---:|
| Avec imputation | 109 971,44 € | 109 923,03 € | 754 | 103 834,67 € | 6 088,36 € | 136 → 144 / 180 |
| Sans imputation chiffrable | 76 475,16 € | 76 334,50 € | 623 | 70 330,90 € | 6 003,60 € | 128 → 136 / 171 |

Le comparateur est le meilleur plan déjà exporté avec ouverture au 2 novembre. Les gains additionnels sont de 48,41 € et 140,66 €. Le rechargement exact à dates fixes sur les journées éligibles a confirmé les coûts du plan retenu. Le solveur a été interrompu sur plusieurs couples et les bornes agrégées laissent respectivement 36 et 35 couples ouverts. Les couples avec imputation contribuant le plus à l’écart sont `SUP_00118 × PLT_001`, `SUP_00087 × PLT_004` et `SUP_00011 × PLT_004`.

```powershell
$env:PYTHONPATH = 'src'
$seed = 'results/impact-anticipation/affectations.csv'
$relaxed = 'SUP_00118:PLT_001','SUP_00087:PLT_004','SUP_00011:PLT_004','SUP_00145:PLT_004','SUP_00045:PLT_001','SUP_00080:PLT_004','SUP_00024:PLT_004','SUP_00085:PLT_004','SUP_00129:PLT_003','SUP_00149:PLT_004','SUP_00010:PLT_004','SUP_00102:PLT_001','SUP_00014:PLT_004'
$targets = $relaxed | ForEach-Object { '--couple'; $_ }
python milestone4/optimisation_cout.py --earliest 2026-11-02 --joint --workers 4 --bound-seconds 45 --couple SUP_00129:PLT_003 --couple SUP_00136:PLT_001 --couple SUP_00144:PLT_001 --couple SUP_00118:PLT_004 --incumbent $seed --output results/cout-novembre-cibles
python milestone4/optimisation_cout.py --earliest 2026-11-02 --workers 7 --bound-seconds 28 --exact-size 10 @targets --incumbent $seed --output results/cout-novembre-renforce
python milestone4/optimisation_cout.py --earliest 2026-11-02 --workers 5 --bound-seconds 35 --exact-size 11 --couple SUP_00022:PLT_004 --couple SUP_00045:PLT_002 --couple SUP_00080:PLT_001 --couple SUP_00118:PLT_004 --couple SUP_00041:PLT_004 --incumbent $seed --output results/cout-novembre-zones-intermediaires
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 --run results/anticipation-02-nov --run results/cout-novembre-cibles --run results/cout-novembre-renforce --run results/cout-novembre-zones-intermediaires --floors results/optimisation-cout/bornes.csv --output results/novembre-consolide
python milestone4/recharger_exact.py --previous results/novembre-consolide --output results/novembre-consolide-recharge
```

### Chargements compacts pour les trois couples prioritaires

Pour une journée dont toutes les lignes éligibles tiennent dans un camion, toute sous-partie du chargement tient aussi. On énumère les combinaisons de tranches tarifaires par camion. Une combinaison est écartée si le coût d’un camion portant le poids maximal de cette combinaison est inférieur ou égal à son coût. Comme le tarif est croissant, fusionner cette combinaison ne renchérit aucune affectation et préserve la date. Le coût minimal reste donc accessible. Les combinaisons conservées n’acceptent que des masses dans les bornes de leur tranche. Le modèle détaillé habituel prend le relais si la capacité totale d’une journée ne satisfait pas la condition.

Les trois couples pilotes ont les mêmes lignes dans les deux périmètres. La résolution est partagée sur la base des identifiants de lignes préparées. La méthode conserve les jours ouvrés, J−7 à J et la semaine témoin du profil. La preuve de coût vaut pour les règles modélisées et dépend de la fermeture de l’écart entre le plan et la borne.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
python -m unittest discover -s milestone4 -p test_partition_hall.py -v
python milestone4/optimisation_cout.py --earliest 2026-11-02 --compact-joint --exact-size 0 --workers 3 --bound-seconds 100 --couple SUP_00118:PLT_001 --couple SUP_00087:PLT_004 --couple SUP_00011:PLT_004 --incumbent results/novembre-consolide/affectations.csv --output results/novembre-tarifs-compacts
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 --run results/novembre-consolide --run results/novembre-tarifs-compacts --floors results/optimisation-cout/bornes.csv --output results/novembre-consolide-compact
```

Les huit tests passent. Sur le pilote, au plus deux camions par jour subsistent dans les combinaisons de tranches. Les trois calculs ont atteint le budget de 100 s sans preuve complète. Pour `SUP_00011 × PLT_004`, le plan gagne 31,32 € et la borne monte de 405,18 €. Le profil devient vendredi et le plan utilise un départ avant le 9 pour 12 lignes. Les deux autres couples conservent leur plan et leur borne précédents. Leur plafond d’amélioration reste respectivement de 512,99 € et 486,41 €.

| Périmètre | Coût faisable | Camions | Écart à la borne | Couples certifiés |
|---|---:|---:|---:|---:|
| Avec imputation | 109 891,71 € | 754 | 5 651,86 € | 144 / 180 |
| Sans imputation chiffrable | 76 303,18 € | 623 | 5 567,10 € | 136 / 171 |

### Reprise de tous les couples ouverts

Le lot reprend les 71 cas couple × périmètre dont le coût faisable dépasse la borne. Les listes de lignes identiques réduisent ce travail à 43 résolutions distinctes. Le mode adaptatif utilise les chargements physiques compacts quand les journées le permettent. Il passe au modèle agrégé lorsque plus de 16 lignes éligibles dépassent ensemble une capacité sur une journée, puis contrôle les camions de tout plan obtenu. Une borne agrégée qui rejoint le coût d’un plan faisable suffit à certifier ce coût.

Un second passage cible huit couples. Pour chaque intervalle retenu, les lignes dont toutes les dates possibles tombent dans cet intervalle y imposent un coût minimal. Ce coût couvre les charges qui occupent un camion entier, puis la capacité tarifaire, le poids de la ligne la plus lourde et le nombre minimal de camions nécessaire au volume restant. Le modèle utilise le plus fort de ces trois derniers planchers. Les contraintes restent valides quel que soit le profil choisi.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$base = 'results/novembre-consolide-compact'
$shared = @('--earliest','2026-11-02','--adaptive','--exact-size','0','--open-from',"$base/couples.csv",'--incumbent',"$base/affectations.csv")
python milestone4/optimisation_cout.py @shared --workers 7 --bound-seconds 55 --output results/novembre-lot-ouvert
python milestone4/optimisation_cout.py @shared --window-cuts --workers 4 --bound-seconds 60 --couple SUP_00118:PLT_001 --couple SUP_00087:PLT_004 --couple SUP_00045:PLT_001 --couple SUP_00080:PLT_004 --couple SUP_00085:PLT_004 --couple SUP_00149:PLT_004 --couple SUP_00094:PLT_004 --couple SUP_00011:PLT_004 --output results/novembre-lot-fenetres
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 --run $base --run results/novembre-lot-ouvert --run results/novembre-lot-fenetres --floors results/optimisation-cout/bornes.csv --output results/novembre-consolide-lot
```

| Périmètre | Coût faisable | Gain sur le plan précédent | Camions | Écart à la borne | Couples certifiés |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 109 526,03 € | 365,68 € | 757 | 3 416,20 € | 157 / 180 |
| Sans imputation chiffrable | 76 059,93 € | 243,25 € | 627 | 3 444,03 € | 149 / 171 |

Les 71 cas attendus figurent dans les résultats du premier lot et 13 couples supplémentaires sont certifiés dans chaque périmètre. Le contrôle indépendant recalcule les coûts et les capacités sur les 1 384 camions des deux plans, avec 8 640 affectations cumulées. Les huit tests passent. Le second passage par fenêtres a été arrêté à 60 s par résolution sans nouvelle certification. Les trois plus gros écarts restants sont communs aux deux périmètres. Ils concernent `SUP_00118 × PLT_001` (512,99 €), `SUP_00087 × PLT_004` (486,41 €) et `SUP_00080 × PLT_004` (399,55 €).

### Recherche d’une preuve au coût strict

Les coûts sont des centimes entiers de la grille avant application du coefficient commun. À partir d’un plan faisable de coût `U`, le solveur peut chercher uniquement les plans de coût inférieur ou égal à `U − 1`. Une preuve d’infaisabilité certifie alors `U`. La recherche réutilise les bornes établies sur les mêmes lignes. Dans le modèle physique compact, le camion principal reçoit les lignes non chargées dans un camion secondaire. Cette représentation retire les variables redondantes sans changer les chargements possibles.

La décomposition par profils traite séparément les 31 sous-ensembles non vides des jours ouvrés. Les affectations incompatibles avec le profil et l’exception initiale disparaissent avant résolution. La borne du couple est le minimum des bornes des 31 sous-problèmes. Une variante autorise temporairement des fractions de charge afin de calculer une borne. Les affectations fractionnaires sont éliminées à l’export et le plan de référence est conservé. Une borne égale au coût du plan entier suffit à le certifier.

Le mode renforcé borne aussi la masse éligible par jour et utilise le nombre minimal de dates qui couvrent les fenêtres du bloc. Il additionne les meilleurs planchers sur des blocs disjoints. Une ligne qui épuise le poids ou le volume disponible est représentée directement par un camion, tout en conservant ses choix de date.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$initial = 'results/novembre-consolide-lot'
$ref = 'results/novembre-consolide-preuve'
$solver = @('--earliest','2026-11-02','--adaptive','--prove-better','--exact-size','0')
$top = @('--couple','SUP_00118:PLT_001','--couple','SUP_00087:PLT_004','--couple','SUP_00080:PLT_004','--couple','SUP_00094:PLT_004')
$proches = @('--couple','SUP_00107:PLT_002','--couple','SUP_00091:PLT_001','--couple','SUP_00053:PLT_001','--couple','SUP_00035:PLT_001','--couple','SUP_00041:PLT_004','--couple','SUP_00145:PLT_004')
python milestone4/optimisation_cout.py @solver --window-cuts --workers 7 --bound-seconds 95 --open-from "$initial/couples.csv" --incumbent "$initial/affectations.csv" --output results/novembre-preuve-stricte
python milestone4/optimisation_cout.py @solver --window-cuts --split-profiles --workers 4 --bound-seconds 7 --open-from "$initial/couples.csv" --incumbent "$initial/affectations.csv" @top --output results/novembre-profils-separes
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 --run $initial --run results/novembre-preuve-stricte --run results/novembre-profils-separes --floors results/optimisation-cout/bornes.csv --output $ref
python milestone4/optimisation_cout.py @solver --window-cuts --split-profiles --fractional-lines --workers 4 --bound-seconds 6 --open-from "$initial/couples.csv" --incumbent "$initial/affectations.csv" @top --output results/novembre-bornes-fractionnelles
python milestone4/optimisation_cout.py @solver --window-cuts --workers 7 --bound-seconds 150 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" --output results/novembre-preuve-prolongee
python milestone4/optimisation_cout.py @solver --window-cuts --split-profiles --workers 4 --bound-seconds 5 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" @proches --output results/novembre-profils-proches
python milestone4/optimisation_cout.py @solver --tight-window-cuts --workers 4 --bound-seconds 100 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" @top --output results/novembre-fenetres-renforcees
python milestone4/optimisation_cout.py @solver --tight-window-cuts --workers 5 --bound-seconds 35 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" --output results/novembre-bornes-blocs
python milestone4/optimisation_cout.py @solver --tight-window-cuts --split-profiles --workers 4 --bound-seconds 3 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" @top --output results/novembre-profils-blocs
python milestone4/optimisation_cout.py @solver --tight-window-cuts --workers 4 --bound-seconds 0.05 --open-from "$ref/couples.csv" --incumbent "$ref/affectations.csv" --output results/novembre-blocs-locaux
$runs = $ref,'results/novembre-preuve-prolongee','results/novembre-profils-proches','results/novembre-bornes-fractionnelles','results/novembre-fenetres-renforcees','results/novembre-bornes-blocs','results/novembre-profils-blocs','results/novembre-blocs-locaux'
$inputs = $runs | ForEach-Object { '--run'; $_ }
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 @inputs --floors results/optimisation-cout/bornes.csv --output results/novembre-consolide-prolonge
```

Les exécutions plafonnées peuvent produire des bornes ou des plans différents selon les ressources disponibles. La consolidation reprend le minimum des coûts faisables et le maximum des bornes compatibles.

| Périmètre | Borne inférieure | Meilleur coût faisable | Camions | Écart restant | Couples certifiés |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 107 668,70 € | 109 429,73 € | 757 | 1 761,03 € | 163 / 180 |
| Sans imputation chiffrable | 74 397,80 € | 75 968,21 € | 627 | 1 570,41 € | 156 / 171 |

Les 45 cas ouverts après le lot précédent ont été repris. La consolidation ferme six cas avec imputation et sept sans. Elle baisse les coûts vérifiés de 96,30 € et 91,72 €, respectivement. Les dix tests passent. Un contrôle indépendant confirme les lignes, les capacités, les dates et les tarifs sur les 1 384 camions des deux scénarios. La preuve globale reste ouverte sur 17 et 15 couples. Les deux plus gros écarts communs sont `SUP_00080 × PLT_004` (292,11 €) et `SUP_00118 × PLT_001` (240,45 €).

### Profils et couples dans une même file parallèle

Les lots précédents répartissaient les couples entre les processus. Chaque processus parcourait ensuite les profils de son couple dans l’ordre. En mode `--parallel-profiles`, les tâches correspondent directement aux couples × profils. Elles partagent une file et un groupe de processus. Les entrées avec les mêmes identifiants de lignes se résolvent une fois pour les deux périmètres.

Chaque profil reçoit le même meilleur coût faisable initial pour son couple. Le meilleur résultat entier parmi les profils devient le plan retenu. Le minimum des bornes des 31 profils constitue la borne du couple. Le registre `profils.csv` permet de vérifier que les 31 profils ont été examinés et d’identifier ceux qui laissent un écart. Un budget de calcul atteint conserve une borne, si disponible, et l’éventuel plan faisable obtenu.

```powershell
$env:PYTHONPATH = 'src'
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
$base = 'results/novembre-consolide-prolonge'
python milestone4/optimisation_cout.py --earliest 2026-11-02 --adaptive --tight-window-cuts --prove-better --split-profiles --parallel-profiles --exact-size 0 --workers 7 --bound-seconds 3 --open-from "$base/couples.csv" --incumbent "$base/affectations.csv" --output results/novembre-profils-paralleles
python milestone4/retenir_meilleurs_couts.py --earliest 2026-11-02 --run $base --run results/novembre-profils-paralleles --floors results/optimisation-cout/bornes.csv --output results/novembre-consolide-parallele
```

La file a traité 527 tâches sur sept processus en 169,28 s. Les 17 jeux de lignes distincts correspondent aux 32 cas ouverts. Le registre contient exactement 31 profils par cas, soit 992 entrées. Les onze tests passent. Le tarif et les capacités des 1 383 camions consolidés ont été recalculés indépendamment.

| Périmètre | Meilleur coût faisable | Gain depuis le plan précédent | Camions | Écart à la borne | Couples certifiés |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 109 345,78 € | 83,95 € | 756 | 1 677,09 € | 163 / 180 |
| Sans imputation chiffrable | 75 895,85 € | 72,36 € | 627 | 1 498,05 € | 156 / 171 |

Les bornes inférieures et le nombre de couples certifiés restent identiques. Les écarts se resserrent grâce aux nouveaux plans. Le temps mesuré décrit ce lot parallèle. Il n’existe pas de mesure séquentielle sur ce même lot avec un budget identique par profil.
