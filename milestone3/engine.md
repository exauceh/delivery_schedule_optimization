# Fonctionnement du moteur d’optimisation

Ce document explique le moteur de consolidation des livraisons du jalon 3. Pour chaque étape, il relie le modèle mathématique écrit dans la première cellule de [milestone3.ipynb](milestone3.ipynb) au code qui l’implémente dans [engine.py](../src/delivery_schedule_optimization/engine.py).

Les numéros de ligne correspondent à la version actuelle de `engine.py`.

---

## 1. Vue d’ensemble du processus

```
besoins (consolidated_dataset.csv) + grille tarifaire + calendrier
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ PREMIER PASSAGE : un MILP par couple × profil N (900 modèles)│ solve_couple, solve_all
 │ choisit les jours ouvrables autorisés et le jour de livraison │
 │ de chaque ligne, en minimisant le coût de transport          │
 └─────────────────────────────────────────────────────────────┘
        │  meilleur plan par couple et par N          best_per_profile
        ▼
   choix du profil : plus petit N au meilleur coût   notebook, section 5.2
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ SECOND PASSAGE : lissage par usine (RG09)                    │ build_options, smooth_plant
 │ évalue chaque ensemble de jours ouvrables de chaque couple,  │
 │ puis sélectionne un ensemble par couple pour réduire le pic  │
 │ quotidien de camions de chaque usine                         │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ CHARGEMENT : lignes complètes dans les camions, livraison par│ load_delivery, _pack
 │ livraison. Moins de camions possibles, puis coût minimal     │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
   contrôles des règles, comparaison avec la situation actuelle, exports   notebook, sections 7 à 11
```

Le notebook contient la préparation des données, les choix métier (règle du profil, contrôles, graphiques, exports) et les paramètres. Le module du moteur contient les modèles d’optimisation. Le moteur doit être un module séparé et importable, car les processus parallèles (`ProcessPoolExecutor`) l’importent dans chaque processus.

---

## 2. Structures de données

| Classe | Ligne | Rôle |
| :--- | :--- | :--- |
| `SolverSettings` | [L30](../src/delivery_schedule_optimization/engine.py#L30) | Paramètres communs à toutes les résolutions : capacité des camions ($Q$, $\bar V$), fenêtre d’anticipation, $\varepsilon$, limite de temps, écart MIP. |
| `CoupleInstance` | [L42](../src/delivery_schedule_optimization/engine.py#L42) | Données d’un couple $c = (f, u)$ : ses lignes $L_c$ avec jour de besoin $i_l$, poids $W_l$ et volume $V_l$, ainsi que le prix $p_{c,k}$ et la borne supérieure $\bar w_k$ de chaque tranche de poids. |
| `CoupleSolution` | [L61](../src/delivery_schedule_optimization/engine.py#L61) | Plan d’un couple pour un profil : jour de livraison de chaque ligne, coût, statut du solveur et jours ouvrables utilisés. |
| `DeliveryLoad` | [L192](../src/delivery_schedule_optimization/engine.py#L192) | Résultat du chargement d’une livraison : numéro de camion de chaque ligne, poids, volume et coût de chaque camion. |
| `CoupleOption` | [L349](../src/delivery_schedule_optimization/engine.py#L349) | Ensemble de jours ouvrables d’un couple, avec son plan, son coût chargé et ses camions par jour. Il constitue l’entrée du lissage. |
| `PlantSmoothing` | [L360](../src/delivery_schedule_optimization/engine.py#L360) | Résultat du lissage d’une usine : option choisie par couple, pic et coût avant et après. |

Le notebook construit une `CoupleInstance` par couple dans `build_instances` (notebook, section 3). Le prix d’un couple dans la tranche $k$ est le prix de base de la grille tarifaire correspondant à sa ligne de distance, multiplié par $(1 + \text{marge}) \times \text{diesel}$.

---

## 3. Premier passage : un MILP par couple et par profil

`solve_couple` ([L77](../src/delivery_schedule_optimization/engine.py#L77)) construit et résout le modèle de la première cellule du notebook pour **un** couple $c$ et **un** profil $N$ (`n_max`).

### 3.1 Pourquoi un problème par couple

Aucune contrainte du premier passage ne relie deux couples : deux fournisseurs ne partagent jamais un camion (RG01), et le coût d’un couple ne dépend que de ses propres lignes. Le problème global est donc la somme de 180 problèmes indépendants. Les résoudre séparément fournit exactement le même optimum qu’un modèle unique beaucoup plus grand, avec une vitesse nettement supérieure.

### 3.2 Ensembles

| Mathématiques | Code | Ligne |
| :--- | :--- | :--- |
| $J$, calendrier des livraisons (jours ouvrés, sans jour férié allemand) | `calendar` → `days` (construit dans le notebook, section 2.2) | [L96](../src/delivery_schedule_optimization/engine.py#L96) |
| $A_l = \{ j \in J : i_l - 7 \le j \le i_l \}$, jours possibles de livraison de la ligne $l$ | `days[first[l]:last[l]]`, obtenu avec `np.searchsorted` | [L98-L99](../src/delivery_schedule_optimization/engine.py#L98-L99) |
| $wd(j)$, jour de la semaine du jour $j$ | `weekday(days)` (0 = lundi) | [L72](../src/delivery_schedule_optimization/engine.py#L72) |
| $K$, tranches de poids | `brackets = range(len(inst.bracket_upper))` | [L115](../src/delivery_schedule_optimization/engine.py#L115) |

Comme $A_l$ est calculé en premier, une variable $z_{c,l,j}$ est créée uniquement pour les jours où la ligne $l$ peut arriver. La fenêtre d’anticipation (RG03) est donc appliquée par construction : aucune variable n’existe pour un jour interdit.

### 3.3 Variables

| Mathématiques | Signification | Code | Ligne |
| :--- | :--- | :--- | :--- |
| $z_{c,l,j} \in \{0,1\}$ | la ligne $l$ est livrée le jour $j$ | `z_{li}_{dj}`, stockée dans `by_line` (par ligne) et `by_day` (par jour) | [L107-L113](../src/delivery_schedule_optimization/engine.py#L107-L113) |
| $x_{c,j} \in \{0,1\}$ | le couple est livré le jour $j$ | `x[dj]` | [L116](../src/delivery_schedule_optimization/engine.py#L116) |
| $F_{c,j} \in \mathbb{N}$ | camions complets le jour $j$, au tarif forfaitaire | `full[dj]` | [L117](../src/delivery_schedule_optimization/engine.py#L117) |
| $y_{c,j,k} \in \{0,1\}$ | le dernier camion du jour $j$ est facturé dans la tranche $k$ | `y[dj, k]` | [L118](../src/delivery_schedule_optimization/engine.py#L118) |
| $s_{c,d} \in \{0,1\}$ | le jour ouvrable $d$ est autorisé pour le couple | `s[d]` | [L119](../src/delivery_schedule_optimization/engine.py#L119) |
| $h_{c,w} \in \{0,1\}$ | la semaine $w$ livre tous les jours ouvrables autorisés | `h[wk]` | [L156](../src/delivery_schedule_optimization/engine.py#L156) |

Les variables ne sont créées que pour les jours présents dans `by_day`, c’est-à-dire les jours où au moins une ligne peut arriver. Cela maintient chaque modèle de petite taille.

### 3.4 Fonction objectif

$$\min \; \sum_{j} \Big( p^{\max}_c \, F_{c,j} + \sum_{k} p_{c,k} \, y_{c,j,k} + \varepsilon \, x_{c,j} \Big)$$

| Terme | Code | Ligne |
| :--- | :--- | :--- |
| $p^{\max}_c F_{c,j}$ : camions complets au tarif forfaitaire (dernière colonne de la grille tarifaire, `prices[-1]`) | `inst.prices[-1] * full[dj]` | [L121](../src/delivery_schedule_optimization/engine.py#L121) |
| $p_{c,k} \, y_{c,j,k}$ : dernier camion facturé au prix de sa tranche | `inst.prices[k] * y[dj, k]` | [L121-L123](../src/delivery_schedule_optimization/engine.py#L121-L123) |
| $\varepsilon \, x_{c,j}$ : 1 centime par jour de livraison, départage favorisant moins de livraisons | `settings.delivery_penalty_eur * lpSum(x)` | [L124](../src/delivery_schedule_optimization/engine.py#L124) |

L’expression `transport` (sans $\varepsilon$) est le coût enregistré dans `model_cost`.

### 3.5 Contraintes

| # | Mathématiques | Signification | Code | Ligne |
| :--- | :--- | :--- | :--- | :--- |
| (1) | $\sum_{j \in A_l} z_{c,l,j} = 1$ | chaque ligne est livrée une seule fois dans sa fenêtre (RG03, RG08) | boucle sur `by_line` | [L126-L127](../src/delivery_schedule_optimization/engine.py#L126-L127) |
| (2) | $z_{c,l,j} \le x_{c,j}$ | aucune ligne n’est assignée à un jour sans livraison | boucle sur `by_day` | [L129-L130](../src/delivery_schedule_optimization/engine.py#L129-L130) |
| (3) | $\sum_l W_l z_{c,l,j} \le Q F_{c,j} + \sum_k \bar w_k y_{c,j,k}$ | le poids du jour tient dans les camions facturés (RG04, RG11) | `settings.truck_kg * full[dj] + ...` | [L131-L133](../src/delivery_schedule_optimization/engine.py#L131-L133) |
| (4) | $\sum_l V_l z_{c,l,j} \le \bar V (F_{c,j} + \sum_k y_{c,j,k})$ | le volume du jour tient dans les camions (RG04) | `settings.truck_m3 * (...)` | [L134-L136](../src/delivery_schedule_optimization/engine.py#L134-L136) |
| (5) | $\sum_k y_{c,j,k} = x_{c,j}$ | une livraison possède exactement un « dernier camion » | | [L137](../src/delivery_schedule_optimization/engine.py#L137) |
| (6) | $F_{c,j} \le M_{c,j} x_{c,j}$ et $x_{c,j} \le \sum_l z_{c,l,j}$ | aucun camion sans livraison, aucune livraison vide ($M_{c,j}$ = nombre de lignes possibles ce jour-là) | | [L138-L139](../src/delivery_schedule_optimization/engine.py#L138-L139) |
| (7) | $x_{c,j} \le s_{c,wd(j)}$ et $\sum_d s_{c,d} \le N$ | livraisons uniquement les jours ouvrables autorisés, au plus $N$ jours (RG05) | | [L140-L141](../src/delivery_schedule_optimization/engine.py#L140-L141) |
| (8) | $x_{c,j} \ge s_{c,wd(j)} + h_{c,w(j)} - 1$ et $\sum_w h_{c,w} \ge 1$ | le profil est atteint : au moins une semaine livre tous les jours ouvrables autorisés | bloc `if profile_reached:` | [L146-L164](../src/delivery_schedule_optimization/engine.py#L146-L164) |

Notes sur le code de chaque contrainte :

- **(3) et (5) constituent ensemble la fonction de coût.** Le solveur paie $F$ camions complets au tarif forfaitaire, puis un dernier camion dans une tranche $k$ dont la borne supérieure $\bar w_k$ couvre le reste du poids. Comme le prix augmente avec la tranche, le solveur choisit toujours la tranche la moins chère qui couvre le reste. Il n’est pas nécessaire de définir une borne inférieure par tranche.
- **(3) et (4) vérifient le total du jour, comme si une ligne pouvait être répartie entre plusieurs camions.** Il s’agit d’une approximation qui accélère le modèle. Le chargement réel, avec des lignes complètes, est effectué ensuite (section 5), et le coût publié en provient.
- **(8) dans le code.** Les jours sont regroupés selon la numérotation des semaines commençant le lundi : `(day_number + 3) // 7` fonctionne car le 1er janvier 1970 était un jeudi. Seules les semaines complètes de l’horizon peuvent être une semaine de référence ([L151-L152](../src/delivery_schedule_optimization/engine.py#L151-L152)). Pour un jour $j$ de la semaine $w$, si $s_{wd(j)} = 1$ et $h_w = 1$, alors $x_j$ doit valoir 1. Si aucune ligne ne peut arriver ce jour-là (aucun `x[dj]`), la semaine ne peut pas être la semaine de référence pour ce jour ouvrable ([L163](../src/delivery_schedule_optimization/engine.py#L163)). Les jours fériés ne figurent pas dans le calendrier et sont donc simplement ignorés.
- **Pourquoi (8) ?** Sans cette contrainte, un couple livré une fois par semaine pourrait conserver cinq jours ouvrables autorisés et sélectionner chaque semaine celui qui coûte le moins cher. Il afficherait alors un profil de 5 tout en réservant des créneaux jamais utilisés.
- **Validation.** Avec `window_days=0` et `profile_reached=False`, chaque ligne ne peut arriver que le jour prévu. Le notebook vérifie que le moteur reproduit ainsi exactement les livraisons actuelles du jalon 2 (notebook, section 4).
- **Jours ouvrables imposés.** L’argument facultatif `weekdays` fixe chaque $s_{c,d}$ à 0 ou 1 ([L142-L144](../src/delivery_schedule_optimization/engine.py#L142-L144)). Il est utilisé uniquement lors du lissage (section 6).

### 3.6 Résolution et lecture du résultat

- **Résolution.** HiGHS est appelé via PuLP avec une limite de temps (`time_limit_s`, 5 s), un écart relatif (`mip_gap`, 1 %) et un seul thread, car le parallélisme est géré à un niveau supérieur ([L167](../src/delivery_schedule_optimization/engine.py#L167)).
- **Lecture du résultat.** Pour chaque ligne, le code récupère le jour dont la variable $z$ vaut 1 ([L170-L175](../src/delivery_schedule_optimization/engine.py#L170-L175)). Si aucune valeur n’est trouvée, aucun plan réalisable n’a été trouvé à temps et une `RuntimeError` est levée.
- **Statut.** Il est conservé dans `CoupleSolution.status` : « Optimal Solution Found » signifie que l’écart est inférieur ou égal à 1 %, tandis que « Solution Found » signifie que la limite de temps a arrêté le solveur après avoir trouvé un plan acceptable.

**Comment le solveur accélère la recherche (séparation et évaluation).** HiGHS résout d’abord la relaxation linéaire, dans laquelle chaque variable binaire peut prendre une valeur fractionnaire. Cela fournit une borne inférieure du coût. Le solveur fixe ensuite les variables fractionnaires une par une : par exemple, « ligne 12 le lundi » vaut 1 dans une branche et 0 dans l’autre. Il élimine chaque branche dont la borne inférieure dépasse déjà le meilleur plan trouvé. Il s’arrête lorsque le meilleur plan se situe à moins de `mip_gap` de la borne inférieure.

La formulation avec $F$ (camions complets) et une tranche pour le dernier camion $y$ a été retenue parce que sa relaxation est proche du coût réel. C’est pourquoi le solveur élimine rapidement de nombreuses branches. Une première version, avec une variable binaire par camion et par tranche, a nécessité 36 minutes.

---

## 4. Exécution des 900 problèmes et choix du profil

### 4.1 Résolution parallèle

`solve_all` ([L322](../src/delivery_schedule_optimization/engine.py#L322)) crée une tâche par couple et par profil $N \in \{1, \dots, 5\}$, soit 180 × 5 = 900 tâches. Elles sont envoyées à un `ProcessPoolExecutor` avec un processus par cœur de processeur (`workers`).

Chaque tâche exécute `_solve_task` ([L310](../src/delivery_schedule_optimization/engine.py#L310)) : si le solveur ne trouve aucun plan dans le délai imparti (ce qui peut arriver à un grand couple avec $N = 1$ lorsque la machine est occupée), le problème est résolu une seconde fois avec une limite égale à 6 fois la limite initiale (`RETRY_TIME_FACTOR`).

Les tâches sont triées de la plus grande à la plus petite ([L332](../src/delivery_schedule_optimization/engine.py#L332)). Les problèmes longs démarrent en premier, tandis que les plus courts remplissent les espaces restants à la fin, ce qui réduit la durée totale.

### 4.2 Meilleur plan par profil

`best_per_profile` ([L291](../src/delivery_schedule_optimization/engine.py#L291)) utilise une propriété simple : un plan utilisant $m$ jours ouvrables reste valide pour tout profil $N \ge m$. Pour chaque $N$, la fonction conserve le plan le moins cher parmi tous les plans résolus pour le couple qui utilisent au plus $N$ jours ouvrables.

Cela produit deux effets :
- Le coût ne peut jamais augmenter avec $N$, même lorsque le solveur atteint sa limite de temps pour un $N$ et trouve un meilleur plan pour un autre.
- De nombreux plans sont « repris d’un autre N » (`seconds = 0`) : le plan résolu pour $N = 3$ peut utiliser seulement 2 jours ouvrables, puis être également retenu pour $N = 2$ s’il est moins cher.

### 4.3 Règle du profil (notebook, section 5.2)

Pour chaque couple, le notebook conserve le **plus petit $N$ dont le coût reste dans `profile_tolerance` du meilleur coût du couple** (0 par défaut, donc le plus petit $N$ atteignant le meilleur coût). Avec la contrainte (8), le nombre de jours ouvrables autorisés du plan choisi correspond également à sa fréquence hebdomadaire maximale.

Cette règle (RG07) constitue un choix métier. Le coût de chaque $N$ est exporté (`cost_N1` à `cost_N5` dans `optimized_couples.csv`), afin que la règle puisse être modifiée sans relancer le solveur.

---

## 5. Chargement : lignes complètes dans les camions

### 5.1 De la sortie d’optimisation au chargement

L’optimisation décide **du jour** où chaque ligne est livrée. Le chargement décide ensuite **du camion** dans lequel elle voyage.

**Ce que renvoie l’optimisation.** Pour chaque couple, l’optimisation (premier passage puis lissage) renvoie un plan, une `CoupleSolution` :

| Champ | Contenu | Exemple : SUP_00085 × PLT_004 |
| :--- | :--- | :--- |
| `delivery_days` | le **jour de livraison de chaque ligne**, dans l’ordre de `line_ids` | ligne MAT_000298 nécessaire le 19/11 → lundi 16/11 |
| `weekdays` | les jours ouvrables autorisés, donc le profil | `(0,)` = lundi, profil 1 |
| `model_cost` | coût estimé par le modèle, avec vérification de capacité sur le total du jour | utilisé uniquement pour choisir le profil |

Le plan n’indique **pas** dans quel camion chaque ligne est placée. Il garantit uniquement que, chaque jour, la charge totale tient dans le nombre de camions comptabilisés. Dans le modèle, un « camion complet » ($F_{c,j}$) est facturé au tarif forfaitaire et supposé transporter une capacité complète (24,5 t ou 110,4 m³), comme si une ligne pouvait être divisée. Seul le dernier camion d’une livraison est partiellement rempli.

**Comment le plan arrive au chargement** (notebook, section 7) :

1. `delivery_dates(plans)` rassemble les plans de tous les couples. Le résultat fournit le jour de livraison des 4 389 lignes.
2. `plan = needs.assign(delivery_date=...)` ajoute cette colonne aux besoins. Chaque ligne conserve son matériau, son point de déchargement, son poids et son volume.
3. `load_plan(plan)` groupe les lignes par **couple × jour de livraison**, ce qui forme une livraison. Pour chaque livraison, il appelle `load_delivery` avec les poids et volumes de ses lignes.

**Ce que fait le chargement pour une livraison :**

- **Si tout tient dans un seul camion** (≤ 24,5 t et ≤ 110,4 m³) : un camion, sans calcul. C’est le cas d’environ 600 des 654 livraisons.
- **Sinon**, un petit MILP répartit les lignes complètes dans le nombre minimal de camions, puis au coût minimal (section 5.2).
- **Dans les deux cas**, chaque camion est facturé individuellement selon son poids.

La fonction renvoie le **numéro de camion de chaque ligne**, ainsi que le poids, le volume et le coût de chaque camion.

Comme les lignes ne sont jamais divisées, une livraison peut contenir plusieurs camions partiellement remplis. Par exemple, SUP_00046 × PLT_004 le 25/11 (58,3 t) nécessite 3 camions de 24,4 t, 18,6 t et 15,2 t : sa ligne de 15,2 t ne peut être combinée avec aucune des deux autres. Ce cas est rare (2 des 53 livraisons multi-camions), car la plupart des lignes sont petites par rapport à un camion.

**Exemple : SUP_00085 × PLT_004, lundi 16/11**

```
Optimisation : 31 lignes (besoins du 16/11 au 20/11) → livrées lundi 16/11
Chargement : 9,7 t et 28 m³ → tout tient dans un camion → camion n° 1, 331 €
```

**Résultats générés :**

| Résultat | Construction | Fichier |
| :--- | :--- | :--- |
| Ligne → jour + camion | `delivery_date` (optimisation) + `truck_no` (chargement) | `optimized_lines.csv` |
| Camion : poids, volume, taux de remplissage, coût | chargement | `optimized_trucks.csv` |
| Matériaux de chaque camion (sortie 2) | lignes regroupées par camion | `optimized_truck_materials.csv` |
| Livraison : camions et coût | somme des camions | `optimized_deliveries.csv` |
| Coût total et économies | somme des coûts des camions | `optimized_kpis.csv` |

**Le coût publié provient toujours du chargement, jamais du modèle.** Le modèle choisit les jours ; le chargement détermine les camions réels et leur prix réel.

### 5.2 Modèle de chargement

Le premier passage vérifie la capacité sur le total de chaque jour. Une ligne n’est jamais divisée, car son volume n’est pas divisible. `load_delivery` ([L260](../src/delivery_schedule_optimization/engine.py#L260)) charge donc les lignes complètes d’une livraison (un couple × un jour) dans les camions, puis facturer chaque camion selon son propre poids.

| Étape | Code | Ligne |
| :--- | :--- | :--- |
| Une ligne plus grande qu’un camion provoque une erreur | `ValueError` | [L272](../src/delivery_schedule_optimization/engine.py#L272) |
| Tout tient dans un camion : un camion, aucun MILP (majorité des livraisons) | `if weights.sum() <= ...` | [L274](../src/delivery_schedule_optimization/engine.py#L274) |
| Sinon, essayer un nombre croissant d’emplacements, à partir de la borne inférieure de capacité | `lower`, `for slots in ...` | [L283-L287](../src/delivery_schedule_optimization/engine.py#L283-L287) |
| MILP de chargement pour un nombre donné d’emplacements | `_pack` | [L214](../src/delivery_schedule_optimization/engine.py#L214) |

MILP de chargement dans `_pack` :

| Mathématiques | Code | Ligne |
| :--- | :--- | :--- |
| $a_{l,b} \in \{0,1\}$ : ligne $l$ dans le camion $b$ ; $t_b$ : le camion $b$ est utilisé ; $y_{b,k}$ : tranche du camion $b$ | `a`, `used`, `y` | [L225-L227](../src/delivery_schedule_optimization/engine.py#L225-L227) |
| $\min \; P \sum_b t_b + \sum_{b,k} p_k y_{b,k}$, avec $P$ supérieur à tout écart de prix | `truck_penalty * lpSum(used) + ...` | [L229-L230](../src/delivery_schedule_optimization/engine.py#L229-L230) |
| $\sum_b a_{l,b} = 1$ : une ligne dans exactement un camion | | [L232](../src/delivery_schedule_optimization/engine.py#L232) |
| $\sum_k y_{b,k} = t_b$ : un camion utilisé possède une tranche | | [L234](../src/delivery_schedule_optimization/engine.py#L234) |
| $\sum_l W_l a_{l,b} \le \sum_k \bar w_k y_{b,k}$ : poids dans la tranche | | [L235-L237](../src/delivery_schedule_optimization/engine.py#L235-L237) |
| $\sum_l V_l a_{l,b} \le \bar V t_b$ : volume | | [L238](../src/delivery_schedule_optimization/engine.py#L238) |
| $t_b \le t_{b-1}$ : camions utilisés dans l’ordre (supprime les solutions identiques obtenues par permutation) | | [L239-L240](../src/delivery_schedule_optimization/engine.py#L239-L240) |

**Pourquoi le nombre de camions est prioritaire :** la grille tarifaire n’est pas sous-additive. Par exemple, 1 050 kg dans un camion coûtent plus cher que deux camions de 1 000 kg et 50 kg. Une minimisation pure du coût pourrait ajouter des camions pour obtenir des tranches moins chères, ce qui n’a pas de sens opérationnel. Le grand coefficient $P$ rend un camion supplémentaire plus coûteux que tout écart de prix. Le solveur minimise donc d’abord le nombre de camions, puis le coût.

Après la résolution, les camions sont renumérotés afin que le camion 1 soit le plus lourd ([L246-L248](../src/delivery_schedule_optimization/engine.py#L246-L248)), puis chaque camion est facturé avec `truck_cost` ([L206](../src/delivery_schedule_optimization/engine.py#L206)) : la tranche la moins élevée dont la borne supérieure inclusive couvre son poids.

Dans le notebook, `load_plan` (notebook, section 3) appelle `load_delivery` pour chaque livraison d’un plan. Ce coût chargé est le coût publié, aussi bien pour la situation actuelle que pour le plan optimisé. L’estimateur du jalon 2, qui considérait la charge comme divisible, est conservé uniquement pour mesurer son biais (+0,14 %).

---

## 6. Second passage : lissage par usine (RG09)

### 6.1 Le problème

Le premier passage choisit les jours ouvrables de chaque couple sans tenir compte des autres couples de la même usine. De nombreux couples disposent de plusieurs jours ouvrables au même coût, et le solveur en sélectionne un arbitrairement, souvent toujours le même. Avant lissage, PLT_002 recevait 57 camions le vendredi 20/11 et 2 à 4 camions les autres jours de cette semaine.

Le lissage est la seule règle reliant les couples d’une usine. L’intégrer au modèle du premier passage nécessiterait un modèle très volumineux par usine (jusqu’à 64 couples et 1 500 lignes). Cela ferait perdre le parallélisme et dégraderait la relaxation. Il est donc réalisé dans un second passage, qui conserve le profil $N_c$ de chaque couple et ne modifie que ses jours ouvrables.

### 6.2 Étape 1 : évaluer chaque ensemble de jours ouvrables (`build_options`)

`build_options` ([L397](../src/delivery_schedule_optimization/engine.py#L397)) construit l’ensemble $O_c$ de chaque couple :
- **option 0** : plan du premier passage $o^1_c$, chargé avec `evaluate_plan` ([L372](../src/delivery_schedule_optimization/engine.py#L372)) ;
- **chaque autre combinaison de $N_c$ jours ouvrables** (5 options pour $N_c = 1$, 10 pour $N_c = 2$ ou 3) est résolue avec `solve_couple(..., weekdays=combo)`. Le modèle du premier passage est utilisé avec les variables $s_{c,d}$ imposées, puis le plan est chargé camion par camion (`_solve_option`, [L385](../src/delivery_schedule_optimization/engine.py#L385)).

Pour chaque option, le code conserve son coût chargé $\kappa_{c,o}$ (`CoupleOption.cost`) et son nombre de camions chaque jour $\tau_{c,o,j}$ (`CoupleOption.days`, `CoupleOption.trucks`). Environ 1 000 petites résolutions s’exécutent en parallèle, du plus long au plus court, en environ 40 s. Un ensemble de jours ouvrables sans plan réalisable (par exemple celui qui ne peut satisfaire la contrainte (8)) est ignoré.

### 6.3 Étape 2 : choisir un ensemble de jours ouvrables par couple (`smooth_plant`)

`smooth_plant` ([L431](../src/delivery_schedule_optimization/engine.py#L431)) résout, pour une usine $u$, un petit MILP en deux étapes :

| Mathématiques | Code | Ligne |
| :--- | :--- | :--- |
| $u_{c,o} \in \{0,1\}$ : le couple $c$ reçoit l’ensemble de jours ouvrables $o$ | `u[c, o]` | dans `solve` |
| $P_u \ge 0$ : camions reçus le jour le plus chargé | `peak` | dans `solve` |
| $\sum_o u_{c,o} = 1$ : un ensemble de jours ouvrables par couple | | [L462](../src/delivery_schedule_optimization/engine.py#L462) |
| $\sum_c \sum_o \tau_{c,o,j} u_{c,o} \le P_u$ pour chaque jour $j$ | `load[day]` | [L464-L469](../src/delivery_schedule_optimization/engine.py#L464-L469) |
| $\sum_c \sum_o \kappa_{c,o} u_{c,o} \le (1 + \text{tol}) \sum_c \kappa_{c,o^1_c}$ : budget de coût | `budget` | [L445](../src/delivery_schedule_optimization/engine.py#L445), [L470](../src/delivery_schedule_optimization/engine.py#L470) |
| Étape 1 : $\min P_u$ | `solve(None)` | [L478](../src/delivery_schedule_optimization/engine.py#L478) |
| Étape 2 : le pic est fixé à $P_u^*$, puis $\min \sum \kappa u$ | `solve(float(peak_after))` | [L480](../src/delivery_schedule_optimization/engine.py#L480) |

$\text{tol}$ correspond à `smoothing_tolerance` (1 % par défaut). L’option 0 de chaque couple respecte toujours le budget, donc le problème possède toujours une solution. Avec au plus environ 60 couples × 10 options par usine, chaque problème est résolu à l’optimalité (écart 0) en moins d’une seconde.

**Ce que cette approche abandonne par rapport à un grand modèle unique.** Le pic ne dépend que des ensembles de jours ouvrables, tandis que le coût et les camions de chaque ensemble sont calculés exactement. Le second passage constitue donc l’optimum exact du lissage, sous deux simplifications :
1. le profil $N_c$ de chaque couple est fixé par le premier passage ;
2. pour un ensemble de jours ouvrables donné, le placement le moins cher des lignes est conservé. Les lignes ne sont pas déplacées entre les jours uniquement pour lisser davantage.

### 6.4 Résultat

| Usine | Pic avant (camions/jour) | Pic après | Coût supplémentaire |
| :--- | ---: | ---: | ---: |
| PLT_001 | 15 | 8 | +0,08 % |
| PLT_002 | 57 | 19 | +0,98 % |
| PLT_003 | 10 | 6 | +0,53 % |
| PLT_004 | 15 | 8 | +0,70 % |

86 couples changent de jours ouvrables. Le coût total passe de 109 623 € à 110 265 € (+0,6 %). Sans lissage, le premier passage à lui seul augmente déjà le pic quotidien maximal (54 camions aujourd’hui, 57 après le premier passage), car il concentre les livraisons sur quelques jours ouvrables.

Le notebook représente les camions reçus chaque jour avant et après lissage (section 6), puis compare la situation actuelle, le premier passage et le plan lissé dans des graphiques : coût total, livraisons, camions, coût et pic par usine, ainsi que camions par jour ouvrable (section 10.1). La section 7 vérifie qu’aucun pic n’a augmenté.

---

## 7. Paramètres

Tous les paramètres se trouvent dans `OptimConfig` (notebook, section 2.1) et sont transmis au moteur via `SolverSettings`.

| Paramètre | Valeur | Effet |
| :--- | :--- | :--- |
| `truck_kg`, `truck_m3` | 24 500 kg, 110,4 m³ | $Q$ et $\bar V$, capacité d’un camion (RG04). |
| `max_anticipation_days` | 7 | Taille de la fenêtre $A_l$ (RG03). |
| `frequencies` | 1 … 5 | Profils $N$ résolus lors du premier passage. |
| `profile_tolerance` | 0 | Règle du profil : plus petit $N$ restant dans cette tolérance du meilleur coût (RG07, choix métier). |
| `smoothing_tolerance` | 0,01 | Coût supplémentaire accepté par usine pour le lissage (RG09, choix métier). |
| `delivery_penalty_eur` | 0,01 | $\varepsilon$ : départage favorisant moins de livraisons. Il n’est pas inclus dans le coût publié. |
| `time_limit_s` | 5 | Limite de temps de chaque MILP. Avec 30 s, le coût varie d’environ 0,1 % et l’exécution dure 2,5 fois plus longtemps. Avec 3 s, un grand couple n’a aucun plan pour $N = 1$. |
| `mip_gap` | 0,01 | Le solveur s’arrête lorsque son plan est prouvé à moins de 1 % de l’optimum. |
| `workers` | None | Nombre de processus parallèles ; `None` = un processus par cœur de processeur. |

**Reproductibilité.** Lorsqu’une résolution s’arrête sur sa limite de temps, le plan dépend du chemin suivi par le solveur à cet instant. Les résultats peuvent donc varier d’environ 0,1 % d’une exécution à l’autre.

---

## 8. Validation et contrôles

| Contrôle | Emplacement |
| :--- | :--- |
| Avec une fenêtre de 0 jour, le moteur reproduit exactement les 1 665 livraisons actuelles, et l’estimateur du jalon 2 retrouve 176 979 € | notebook, section 4 |
| Chaque ligne est livrée une fois (RG08), dans sa fenêtre (RG03), un jour ouvrable (RG10) | notebook, section 7 |
| Chaque livraison se situe un jour ouvrable autorisé pour son couple ; une semaine ne contient jamais plus de livraisons que le profil ; la fréquence hebdomadaire maximale correspond au profil (RG05) | notebook, section 7 |
| Aucun camion n’est surchargé et chaque ligne est placée dans un seul camion | `load_plan`, notebook, section 3 |
| Le lissage conserve chaque profil et ne relève jamais le pic d’une usine (RG09) | notebook, sections 6 et 7 |
| La sortie 2 ne perd aucune ligne ni aucune masse | notebook, section 9 |

---

## 9. Temps d’exécution

| Étape | Durée (12 cœurs) |
| :--- | ---: |
| Données, calendrier, validation avec une fenêtre de 0 jour | environ 5 s |
| Premier passage : 900 MILP | environ 105 s |
| Lissage : environ 1 100 ensembles de jours ouvrables évalués, puis 4 MILP d’usine | environ 40 s |
| Chargement, contrôles, exports | environ 5 s |
| **Notebook complet** | **environ 2 min 40 s** |
