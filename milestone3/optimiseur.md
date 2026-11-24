# L'optimiseur des livraisons (Milestone 3)

Ce document explique **comment l'optimiseur construit le plan de livraison**, étape par étape, avec des liens vers le code de [`planner.py`](../src/delivery_schedule_optimization/planner.py). Le notebook [`candidat_max.ipynb`](candidat_max.ipynb) appelle ces fonctions et affiche les résultats.

**Objectif :** baisser le coût de transport sans jamais livrer en retard, sans livrer plus de 7 jours en avance, et sans dépasser la capacité d'un camion. Chaque couple fournisseur × usine livre sur un **ensemble fixe de jours** de la semaine (son profil), et les jours de livraison sont répartis entre les couples d'une même usine (lissage).

## Vue d'ensemble

```
dataset consolidé
   │
   ├─ 0. Entrées : besoins, grille tarifaire, jours fériés, situation actuelle
   │
   ├─ 1. Pour chaque couple (180) et chaque ensemble de jours (31) :
   │       a. meilleurs jours de départ  → plus court chemin dans un graphe (exact)
   │       b. meilleure affectation des besoins → recherche locale
   │     = les plans candidats du couple (2 209 au total, environ 12 par couple)
   │
   ├─ 2. Choix du plan de chaque couple : le moins cher (RG07)
   │
   ├─ 3. Lissage par usine : un plan par couple, choisi pour tous les couples
   │     de l'usine à la fois → programme linéaire en nombres entiers (HiGHS)
   │
   └─ 4. Sorties (profils, camions, contenu des camions, saving) et contrôles
```

**Pourquoi découper ainsi :** un camion ne mélange jamais deux couples (RG01), donc **sans lissage, les couples sont indépendants**. Chacun est planifié seul, en parallèle. Le lissage est la seule étape qui regarde plusieurs couples ensemble (ceux d'une même usine).

| Étape | Fonction principale | Méthode | Garantie |
| :--- | :--- | :--- | :--- |
| 1a | [`optimal_plan`](../src/delivery_schedule_optimization/planner.py#L329-L434) | programmation dynamique (plus court chemin) | optimal pour un ensemble de jours, avec RG06 stricte |
| 1b | [`reassign`](../src/delivery_schedule_optimization/planner.py#L437-L555) | recherche locale | améliore le plan, sans garantie d'optimum |
| 2 | [`choose_profiles`](../src/delivery_schedule_optimization/planner.py#L611-L613) | tri des candidats | le moins cher parmi les candidats |
| 3 | [`smooth`](../src/delivery_schedule_optimization/planner.py#L697-L726) | MILP par usine (HiGHS) + partage du budget | optimal à chaque niveau de lissage |

---

## 0. Les entrées et les paramètres

### Les paramètres : `PlannerConfig`

Tous les réglages métier sont dans [`PlannerConfig`](../src/delivery_schedule_optimization/planner.py#L53-L81). On les change dans le notebook, sans toucher au code.

| Paramètre | Défaut | Rôle |
| :--- | :--- | :--- |
| `truck_kg`, `truck_m3` | 24 500 kg, 110,4 m³ | capacité d'un camion (RG04) |
| `margin`, `diesel_surcharge` | 2,3923, 1,1943 | prix d'un camion = grille × (1 + marge) × diesel (RG11) |
| `max_advance_days` | 7 | avance maximale, en jours calendaires (RG03) |
| `allowed_frequencies` | 1 à 5 | profils autorisés (RG05) |
| `holiday_country` | DE | jours fériés allemands (RG10) |
| `strict_coverage` | `False` | `False` : RG06 assouplie, un besoin peut partir avec tout départ de sa fenêtre (étape 1b) |
| `smoothing`, `smoothing_budget` | `True`, 0 | lissage activé ; surcoût accepté pour lisser (0 = lissage gratuit) |
| `overflow_first` | `False` | `True` : le moins de départs à plusieurs camions avant le coût, préférence du client (voir étape 2) |
| `workers` | tous les cœurs | les couples sont calculés en parallèle |

### Les données

- **Les besoins** : [`load_lines`](../src/delivery_schedule_optimization/planner.py#L84-L101) lit le dataset consolidé (`data/consolidated_dataset_max.csv`). Une ligne = un besoin (date, fournisseur, material, usine, quai). La fonction vérifie qu'aucune ligne ne dépasse un camion et qu'un fournisseur n'a qu'une distance. Elle marque les lignes **imputées** par un camion plein (`imputed_full_truck`) : poids ou volume inconnu, complété par 24 500 kg ou 110,4 m³.
- **La grille tarifaire** : [`load_rate_grid`](../src/delivery_schedule_optimization/planner.py#L104-L110) et [`Tariff`](../src/delivery_schedule_optimization/planner.py#L113-L124). Le prix d'**un** camion dépend de la tranche de distance du fournisseur et de la tranche de poids du camion. La lecture est « jusqu'à » (`searchsorted`, `side="left"`) : 1 000 kg tombe dans la tranche ≤ 1 000 ; 1 001 kg dans la suivante. Au-delà de 4 000 kg, le prix est forfaitaire.
- **Les jours fériés** : [`public_holidays`](../src/delivery_schedule_optimization/planner.py#L127-L129) (calendrier allemand). Aucun entre le 02/11 et le 18/12.
- **Le début du plan** : [`plan_start`](../src/delivery_schedule_optimization/planner.py#L137-L139). Le client accepte que les premiers besoins partent eux aussi jusqu'à 7 jours avant leur date. Le premier besoin étant le lundi 09/11, le plan commence le **lundi 02/11**. Une fenêtre [date − 7 ; date] compte 8 jours et contient donc chaque jour de la semaine : **chaque besoin a toujours un jour de son profil dans sa fenêtre**, même la première semaine.
- **La situation actuelle** : [`load_baseline`](../src/delivery_schedule_optimization/planner.py#L729-L742) lit les livraisons du Milestone 2 (une par couple et par jour de besoin), puis [`current_situation`](../src/delivery_schedule_optimization/planner.py#L745-L775) les **recharge camion par camion comme le plan** (lignes entières, chaque camion payé sur son poids). La référence et le plan sont ainsi mesurés de la même façon : 1 665 livraisons, 1 741 camions, 176 725 €.

### Le chargement d'une livraison : `pack`

[`pack`](../src/delivery_schedule_optimization/planner.py#L196-L211) range les lignes d'un départ dans des camions :

1. les lignes sont triées de la plus « remplissante » à la moins remplissante (le plus grand des deux ratios poids / 24 500 et volume / 110,4) ;
2. chaque ligne va dans le **premier camion où elle tient** (poids et volume) ; sinon un nouveau camion est ouvert.

C'est l'heuristique *first-fit decreasing*. Une ligne n'est jamais coupée (RG04). Chaque camion est ensuite payé selon son propre poids.

---

## 1. Les plans candidats de chaque couple

Pour **chaque couple** et **chaque ensemble de jours** de la semaine, l'optimiseur calcule le meilleur plan possible. C'est le rôle de [`candidate_plans`](../src/delivery_schedule_optimization/planner.py#L559-L585), qui répartit les couples entre les cœurs du processeur, et de [`couple_candidates`](../src/delivery_schedule_optimization/planner.py#L588-L599), qui traite un couple :

```python
for size in sorted(config.allowed_frequencies):              # profils 1 à 5
    for days in itertools.combinations(range(5), size):       # les 31 ensembles de jours
        plan = optimal_plan(group, days, ...)                 # 1a. jours de départ (exact)
        plan = reassign(group, plan, ...)                     # 1b. affectation des besoins
        plans.setdefault((plan.profile, tuple(plan.dates.tolist())), plan)       # sans doublons
```

Un ensemble est **éliminé** si aucun plan ne respecte la règle des mêmes jours : aucun chemin n'utilise tous les jours de l'ensemble avec au moins une semaine complète. Les **doublons** sont retirés : si le meilleur plan pour {lundi, mercredi} n'utilise que le lundi, il est identique à celui de {lundi}. Résultat : **2 209 plans pour 180 couples**, en 7 s environ.

### Ce qu'est un plan : `CouplePlan`

Un plan ([`CouplePlan`](../src/delivery_schedule_optimization/planner.py#L215-L246)) donne pour chaque ligne du couple son **jour de livraison**, puis ses camions et son coût. Il est calculé par [`evaluate`](../src/delivery_schedule_optimization/planner.py#L249-L277), qui charge chaque départ avec `pack` et le paie avec `Tariff`.

- **Profil** ([`frequency`](../src/delivery_schedule_optimization/planner.py#L227-L230)) = le plus grand nombre de livraisons faites en une semaine. « 3 2 1 2 1 3 » sur 6 semaines = profil 3.
- **Règle des mêmes jours** ([`same_days`](../src/delivery_schedule_optimization/planner.py#L243-L246)) : le couple livre toujours sur le même ensemble de jours, et la taille de cet ensemble est égale au profil. Avec {lundi, mercredi, vendredi}, une semaine peut livrer lundi + vendredi et une autre seulement mercredi, mais au moins une semaine doit livrer les trois.
- **Départs en débordement** (`overflow_departures`) : les départs dont les lignes mesurées (non imputées) ne tiennent pas dans un camion.

### 1a. Les jours de départ : un plus court chemin dans un graphe

[`optimal_plan`](../src/delivery_schedule_optimization/planner.py#L329-L434) trouve les meilleurs jours de départ d'un couple pour un ensemble de jours donné, **sans essayer toutes les combinaisons**.

**L'idée qui rend le graphe possible (RG06 stricte) :** un besoin part avec le **dernier départ à sa date ou avant**. Une fois les dates de départ choisies, l'affectation est donc automatique ([`evaluate_departures`](../src/delivery_schedule_optimization/planner.py#L280-L290)) :

```
départs choisis :  lun 09        jeu 12                lun 16
besoins :          09 10 11  |   12 13 (14 15)    |    16 ...
                   └ départ 09 ┘ └ départ 12 ────┘     └ départ 16
```

Le départ du 09 emporte les besoins datés du 09 jusqu'au départ suivant (exclu). **Le coût d'un départ ne dépend que de lui et du départ suivant.** Le coût total est une somme de « tronçons », donc le meilleur plan est un plus court chemin. C'est le modèle du *dynamic lot sizing with demand time windows* (Lee, Çetinkaya et Wagelmans, 2001).

**Le graphe :**

| Élément | Signification | Code |
| :--- | :--- | :--- |
| Nœud | un jour de départ possible : un jour de l'ensemble entre le 02/11 et le dernier besoin ; un jour férié est remplacé par le jour ouvré précédent (RG10) | [`departure_nodes`](../src/delivery_schedule_optimization/planner.py#L308-L326) |
| Arête i → j | « je pars le jour i, le départ suivant est j » : le départ i emporte les besoins datés de [i ; j[ | [`segment(i, j)`](../src/delivery_schedule_optimization/planner.py#L351-L373) |
| Poids de l'arête | les camions de ce départ (`pack`), leur prix (`Tariff`), le nombre de camions, l'avance totale | [L362-L371](../src/delivery_schedule_optimization/planner.py#L362-L371) |
| Arête interdite | un besoin emporté serait à plus de 7 jours (RG03) | [L360-L361](../src/delivery_schedule_optimization/planner.py#L360-L361) |

Exemple réel (SUP_00085 → PLT_004, profil {lun, mer, ven}) : **21 nœuds et 60 arêtes** (56 entre deux départs, 4 vers la fin), au lieu de 2²¹ ≈ 2 millions de combinaisons de jours de départ. Même en ignorant les profils (tous les jours ouvrés) : 35 nœuds et 180 arêtes, au lieu d'environ 34 milliards de combinaisons.

**Le calcul** ([`solve`](../src/delivery_schedule_optimization/planner.py#L378-L425)) parcourt les nœuds dans l'ordre des dates et garde, pour chaque nœud, **le meilleur coût pour y arriver** (principe de Bellman) :

```
best[j] = min sur i < j de ( best[i] + segment(i, j) )
```

- **Coupure** ([L399-L401](../src/delivery_schedule_optimization/planner.py#L399-L401)) : on remonte de j vers les départs plus anciens ; dès qu'un départ i viole les 7 jours, tous les départs plus anciens la violent aussi, et la boucle s'arrête. Chaque nœud ne regarde qu'environ une semaine en arrière.
- **Fin** ([L408-L425](../src/delivery_schedule_optimization/planner.py#L408-L425)) : on choisit le meilleur « dernier départ » (qui emporte tous les besoins restants), puis on remonte les prédécesseurs mémorisés pour reconstituer le chemin.
- **Critères comparés dans l'ordre** : départs en débordement (seulement si `overflow_first`), puis coût, puis nombre de camions, puis avance totale.

**La règle des mêmes jours** ([L427-L434](../src/delivery_schedule_optimization/planner.py#L427-L434)) : le premier passage l'ignore. Si le chemin trouvé ne la respecte pas, un second passage refait le calcul avec un **état enrichi** à chaque nœud (jours déjà utilisés, semaine courante, nombre de départs dans la semaine) et ne garde à la fin que les chemins qui utilisent tous les jours de l'ensemble, dont une semaine complète.

**Le début du plan :** les nœuds commencent au 02/11, 7 jours avant le premier besoin. Le premier départ du chemin est un nœud qui n'a aucun besoin daté avant lui. Comme chaque besoin a un jour de l'ensemble dans sa fenêtre, aucun cas particulier n'est nécessaire pour la première semaine.

### 1b. L'affectation des besoins : la recherche locale

Le graphe suppose que chaque besoin part avec le **dernier** départ avant sa date (RG06 stricte). L'équipe a assoupli cette règle : un besoin peut partir avec **n'importe quel départ prévu** de son couple dans sa fenêtre [date − 7 jours ; date]. Cela ouvre des économies (remplir un camion qui part de toute façon), mais le coût ne se découpe plus en tronçons : plus de plus court chemin.

[`reassign`](../src/delivery_schedule_optimization/planner.py#L437-L555) part du plan du graphe et garde les **jours de départ**. Elle déplace des besoins d'un départ à un autre, et ne garde un mouvement que si le coût baisse strictement ([`cost`](../src/delivery_schedule_optimization/planner.py#L459-L469)). Trois mouvements, dans cet ordre :

| Mouvement | Ce qu'il fait | Code |
| :--- | :--- | :--- |
| 1. Vider un départ | toutes les lignes d'un départ vont vers d'autres départs ouverts de leur fenêtre ; le départ disparaît | [L514-L533](../src/delivery_schedule_optimization/planner.py#L514-L533) |
| 2. Déplacer une ligne | une ligne passe à un autre départ si la somme des deux coûts baisse | [L534-L548](../src/delivery_schedule_optimization/planner.py#L534-L548) |
| 3. Déplacer une paire | seulement si 1 et 2 ne trouvent plus rien : deux lignes d'un même départ partent ensemble | [`move_pair`](../src/delivery_schedule_optimization/planner.py#L482-L510) |

**Pourquoi des paires :** le prix d'un camion dépend de sa tranche de poids. Exemple réel (SUP_00041 → PLT_004) : déplacer seule la ligne de 185 kg ou seule celle de 149,6 kg coûte plus cher, mais les déplacer ensemble fait changer de tranche les deux camions et coûte moins cher.

Un mouvement qui viderait un départ n'est accepté que si la règle des mêmes jours reste respectée ([`valid`](../src/delivery_schedule_optimization/planner.py#L474-L478)). La recherche s'arrête quand plus aucun mouvement n'améliore (au plus `reassign_passes` = 50 passes). Le plan amélioré n'est gardé que s'il est moins cher que celui du graphe ([L553-L555](../src/delivery_schedule_optimization/planner.py#L553-L555)).

---

## 2. Le choix du plan de chaque couple (RG07)

[`choose_profiles`](../src/delivery_schedule_optimization/planner.py#L611-L613) prend, pour chaque couple, le candidat le plus petit selon [`selection_key`](../src/delivery_schedule_optimization/planner.py#L602-L606) :

1. le moins de départs en débordement (seulement si `overflow_first`) ;
2. **le coût le plus bas** ;
3. puis le moins de camions, le moins d'avance, le profil le plus bas, et les jours les plus tôt dans la semaine.

Avec les réglages par défaut, c'est donc **le plan le moins cher** du couple. Le profil n'est pas imposé : c'est le résultat du meilleur plan.

---

## 3. Le lissage par usine (RG09)

Chaque couple a souvent **plusieurs plans au même coût** (par exemple lundi, vendredi ou lundi + vendredi). Choisis chacun de leur côté, ces plans peuvent tous tomber le même jour et créer des pics de camions à l'usine. Le lissage choisit les plans de **tous les couples d'une usine ensemble**, pour répartir les camions sur la semaine.

### Le menu de chaque couple

[`smoothing_options`](../src/delivery_schedule_optimization/planner.py#L616-L624) donne les plans que chaque couple peut prendre. Avec les réglages par défaut, **tous ses candidats** sont permis : un couple peut changer de jours, et même de profil, si c'est intéressant.

### L'indicateur de lissage

[`smoothing_indicator`](../src/delivery_schedule_optimization/planner.py#L627-L638) mesure la répartition des camions d'une usine :

```
indicateur = Σ (camions du jour le plus chargé de chaque semaine) + camions du jour le plus chargé de la période
```

Plus il est bas, mieux les camions sont répartis. Il additionne les pics **de chaque semaine** : avec le seul pic de la période, une semaine très chargée cacherait toutes les autres, et le solveur n'aurait plus aucune raison de les lisser.

### Le modèle d'une usine

[`plant_frontier`](../src/delivery_schedule_optimization/planner.py#L641-L694) écrit un programme linéaire en nombres entiers et le résout avec HiGHS (`scipy.optimize.milp`) :

| | Définition |
| :--- | :--- |
| Données | pour chaque plan p : son coût c_p et ses camions reçus chaque jour n_{p,d} |
| Variables | x_p ∈ {0, 1} : le couple prend le plan p ; M_s ≥ 0 : pic de la semaine s ; M ≥ 0 : pic de la période |
| Un plan par couple | Σ x_p = 1 pour chaque couple |
| Pic de la semaine | Σ_p n_{p,d} x_p ≤ M_s pour chaque jour d de la semaine s |
| Pic de la période | M_s ≤ M pour chaque semaine |
| Niveau visé | Σ_s M_s + M ≤ L |
| Objectif | minimiser Σ_p c_p x_p (le coût) |

M_s et M ne sont pas des données mais des variables **auxiliaires** : elles servent seulement à écrire « l'indicateur des plans choisis ≤ L » avec des contraintes linéaires, car un maximum ne s'écrit pas directement dans un MILP. Le solveur peut leur donner une valeur plus haute que le vrai pic, donc le code **recalcule** le vrai indicateur des plans choisis avec `smoothing_indicator`.

### La frontière : resserrer L pas à pas

Le modèle minimise le coût sous la contrainte « indicateur ≤ L ». La boucle ([L687-L694](../src/delivery_schedule_optimization/planner.py#L687-L694)) fait varier L :

1. **sans L** ([`cheapest(None)`](../src/delivery_schedule_optimization/planner.py#L668-L685)) : le plan le moins cher de l'usine, et son indicateur I ;
2. on impose **L = I − 1** et on relance : le plan le moins cher au moins un cran mieux lissé ;
3. on recommence tant que le coût reste dans le budget ;
4. on s'arrête quand le budget est dépassé ou qu'aucun plan n'atteint L.

On obtient la **frontière** de l'usine : pour chaque niveau de lissage, le coût minimal pour l'atteindre. C'est la méthode ε-contrainte. Chaque niveau est résolu à l'optimum (gap 0, limite de 60 s).

Exemple réel (PLT_002, 64 couples, 679 plans) : l'indicateur passe de **163 à 117 sans surcoût** (on choisit seulement entre des plans de même coût), puis chaque cran devient payant, jusqu'à 61 pour +491 €.

### Le partage du budget entre les usines

[`smooth`](../src/delivery_schedule_optimization/planner.py#L697-L726) calcule la frontière de chaque usine, puis les combine. Le budget (`smoothing_budget` × coût sans lissage) est **commun aux 4 usines** ([L711](../src/delivery_schedule_optimization/planner.py#L711)) :

- les frontières sont fusionnées usine par usine en gardant les combinaisons non dominées (aucune autre n'est à la fois moins chère et mieux lissée) ([L714-L722](../src/delivery_schedule_optimization/planner.py#L714-L722)) ;
- on prend la combinaison dont l'indicateur total est le plus bas sans dépasser le budget, puis la moins chère ([L723-L726](../src/delivery_schedule_optimization/planner.py#L723-L726)).

Le budget va donc là où un cran de lissage coûte le moins cher.

| Scénario du notebook | Budget | Effet |
| :--- | :--- | :--- |
| B0 - sans lissage | `smoothing=False` | chaque couple garde son plan le moins cher |
| **B1 - lissage gratuit (retenu)** | 0 | on choisit seulement entre des plans de même coût |
| B2 - petit lissage | 0,5 % | +534 € sur 6 semaines ; jour le plus chargé (toutes usines) : 36 → 13 camions |

---

## 4. Les sorties et les contrôles

- **Sortie 1 (profils)** : [`couple_summary`](../src/delivery_schedule_optimization/planner.py#L847-L896), une ligne par couple (profil, jours, coût avant et après, saving).
- **Sortie 2 (camions et affectations)** : [`export`](../src/delivery_schedule_optimization/planner.py#L795-L827) donne une ligne par camion et une ligne par besoin (camion, jour de livraison, avance, règle appliquée : jour du profil ou férié avancé) ; [`truck_contents`](../src/delivery_schedule_optimization/planner.py#L830-L839) regroupe le contenu de chaque camion par material et quai.
- **Sortie 3 (saving)** : calculée dans le notebook à partir de la référence et du plan.
- **Contrôles** : [`check`](../src/delivery_schedule_optimization/planner.py#L920-L999) revérifie chaque règle sur le plan terminé, **indépendamment de la façon dont il a été construit** : RG01 (un couple par camion), RG02 (jamais de retard), RG03 (7 jours au plus), RG04 (capacité recalculée depuis les lignes), RG05 et la règle des mêmes jours, RG06 (assouplie ou stricte), RG07 (coût dans le budget de lissage), RG08 (chaque ligne livrée une seule fois, quantités conservées par usine et material), RG10 (ni week-end ni jour férié), le début du plan (rien avant le 02/11) et RG11 (totaux et prix de chaque camion recalculés).

### Résultat (6 semaines, imputation max)

| | Aujourd'hui | Plan retenu | Écart |
| :--- | ---: | ---: | ---: |
| Coût | 176,7 k€ | 110,5 k€ | **−66 267 € (−37,5 %)** |
| Camions | 1 741 | 757 | −56,5 % |
| Livraisons | 1 665 | 655 | −60,7 % |

Aucun couple ne paie plus cher qu'aujourd'hui.

---

## Limites

- **Un plan par ensemble de jours.** Le menu du lissage contient le meilleur plan de chaque ensemble, pas tous les plans possibles : un plan un peu plus cher mais mieux réparti, sur le même ensemble de jours, n'est jamais proposé.
- **La recherche locale n'est pas exacte.** Avec RG06 assouplie, l'affectation des besoins est améliorée mais pas prouvée optimale.
- **Le chargement est une heuristique.** `pack` (first-fit decreasing) ne cherche pas la répartition la moins chère entre camions ; il minimise surtout le nombre de camions.
- **L'indicateur ne voit que le jour le plus chargé de chaque semaine.** Une semaine à 10, 10, 10, 2, 2 camions et une semaine à 10, 6, 6, 6, 6 ont le même score.
