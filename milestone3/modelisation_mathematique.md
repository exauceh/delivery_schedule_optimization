# Modélisation mathématique du moteur

Le problème consiste à choisir **la date de départ et le camion de chaque ligne**, de façon à minimiser le coût total. Le calcul se fait par couple fournisseur × usine. Les deux scénarios ont chacun leur ensemble de lignes. Le périmètre sans imputation chiffrable exclut les lignes dont le poids, le volume ou la distance sont inconnus.

## 1. Lignes, dates et profils

Pour un couple, $I$ désigne les lignes à transporter. Chaque ligne $i$ a une date de besoin $J_i$, un poids total $w_i$ et un volume total $v_i$. Une ligne reste entière. $D$ est l’ensemble des jours ouvrés de l’horizon, à partir du 2 novembre 2026, hors jours fériés nationaux allemands. Sa fenêtre de départ est

$$
A_i = \{d \in D \mid 0 \le J_i-d \le 7\}.
$$

Les sept jours sont calendaires. Un profil $P$ est un sous-ensemble non vide des cinq jours ouvrés de la semaine, soit $2^5-1=31$ possibilités. Pour un profil fixé, une date de $A_i$ est régulière si son jour appartient à $P$. L’exception de démarrage autorise aussi un départ hors profil à partir du 9 novembre pour une ligne dont le besoin précède la première date régulière à partir de ce même jour. Avant le 9 novembre, seuls les jours du profil sont admissibles.

## 2. Modèle exact par chargements datés

Fixons un profil $P$. Une colonne $B=(d_B,S_B)$ représente un camion à la date $d_B$ contenant un ensemble non vide $S_B$ de lignes. Une colonne est admissible si chaque ligne peut partir à cette date et si elle respecte les deux capacités

$$
\sum_{i\in S_B} w_i\le 24\,500\ \text{kg},
\qquad
\sum_{i\in S_B} v_i\le 110{,}4\ \text{m}^3.
$$

Le coût $c_B$ est le tarif du couple à la distance donnée et à la tranche du poids total du camion. La dernière tranche s’applique au-delà de 4 000 kg, dans la limite des 24 500 kg. Le coefficient commun $3{,}3923\times1{,}1943$ convertit le tarif de base en coût calculé. Le solveur peut donc travailler en centimes de grille avant d’appliquer ce coefficient.

La variable binaire $x_B$ vaut 1 si le camion est retenu. La variable binaire $h_s$ sélectionne une semaine témoin $s$. On minimise le coût sous les contraintes suivantes.

| | Formulation |
|---|---|
| Objectif | $\min \sum_B c_B x_B$ |
| Une affectation par ligne | $\sum_{B:i\in S_B} x_B=1\quad\forall i\in I$ |
| Une semaine témoin | $\sum_s h_s=1$ |
| Un départ chaque jour du profil pendant cette semaine | $\sum_{B:d_B=d(s,p)} x_B\ge h_s\quad\forall s,\ p\in P$ |

Ici $d(s,p)$ est la date du jour $p$ dans la semaine $s$. Si aucune colonne ne part ce jour-là, la somme vaut zéro et la semaine ne peut pas servir de témoin. Cette règle atteste une semaine où le profil entier est réalisé. Le coût optimal du couple est le minimum sur les 31 profils.

## 3. Comment le moteur résout le modèle

- **Petites journées.** Le moteur énumère les chargements admissibles et utilise les variables $x_B$ ci-dessus. Il peut aussi représenter les camions individuellement, avec des variables d’affectation et de choix de tranche.
- **Journées où toutes les lignes éligibles tiennent ensemble.** Il élimine une combinaison de tranches dès qu’un regroupement en un camion est toujours au moins aussi économique. La masse maximale de la combinaison suffit à ce contrôle, car la grille est non décroissante et le regroupement respecte déjà les capacités. Un chargement fractionné en plusieurs camions reste possible quand il diminue le coût, avec des lignes entières.
- **Journées denses.** La relaxation garde la date de chaque ligne et remplace certains camions individuels par des nombres $n_{dk}$ de camions par tranche $k$ et par date. Avec $a_{id}=1$ si la ligne $i$ est affectée au jour $d$, et $u_k$ le plafond de poids de la tranche, les contraintes agrégées sont

$$
\sum_i w_i a_{id} \le \sum_k u_k n_{dk},
\qquad
\sum_i v_i a_{id} \le 110{,}4\sum_k n_{dk}.
$$

Tout plan physique fournit des valeurs qui satisfont ces contraintes en comptant ses camions. La relaxation permet aussi des répartitions supplémentaires, car elle regroupe les capacités au niveau de la journée. Son optimum est donc une **borne inférieure**. Pour obtenir un plan, le moteur recharge les lignes camion par camion et contrôle les deux capacités, les dates et le tarif de chaque camion.

Le moteur renforce la borne sur des intervalles de dates. Les lignes qui doivent toutes partir dans un intervalle imposent un coût minimal à l’intérieur de celui-ci. Les planchers d’intervalles disjoints peuvent s’additionner.

**Contre-vérification sur les petits cas.** En plus de l’énumération exhaustive, la suite de tests utilise une formulation qui sélectionne des chargements puis leur attribue des dates. Pour tout sous-ensemble $Q$ des jours d’une semaine témoin, soit $N(Q)$ l’ensemble des camions sélectionnés compatibles avec au moins un de ces jours. Le théorème de Hall garantit un camion distinct par jour si et seulement si $|N(Q)|\ge |Q|$ pour tout $Q$. Par exemple, si deux jours dépendent du même camion, le sous-ensemble de ces deux jours échoue au test, même si chaque jour a un candidat.

## 4. Lire la preuve et les gains

Pour un couple, $L$ est la meilleure borne inférieure et $U$ le coût du meilleur plan vérifié. Le coût optimal $C^*$ satisfait $L\le C^*\le U$. Une recherche exhaustive qui prouve impossible tout plan d’un coût au plus $U-1$ centime de grille certifie le coût $U$. Après une recherche divisée par profils, la borne du couple est le minimum des bornes de ses 31 profils. On peut ensuite retenir la plus forte borne fournie par les exécutions compatibles.

Dans la génération indépendante des plans, les coûts et les bornes des couples s’additionnent. La marge d’amélioration maximale est $U-L$. L’économie calculée est la différence entre le coût de la référence reconstruite aux dates de besoin et $U$, dans le même périmètre.

## 5. Sélection des plans avec plafonds de réception

Le lissage utilise un catalogue de plans complets. Pour un couple $g$, $\mathcal P_g$ contient uniquement les plans qui couvrent ses lignes, respectent les capacités et partent tous sur les jours du profil. Soit $z_{gp}\in\{0,1\}$ le choix du plan $p$, $c_{gp}$ son coût et $n_{gpd}$ son nombre de camions le jour $d$. Le plafond $M_f$ est le pic quotidien de l’usine $f$ dans le plan de référence d’Arianne.

$$
\sum_{p\in\mathcal P_g} z_{gp}=1,\qquad
\sum_{g:\,\mathrm{usine}(g)=f}\ \sum_{p\in\mathcal P_g} n_{gpd}\,z_{gp}\le M_f
\quad\forall f,d.
$$

Le solveur minimise d’abord $\sum_{g,p} c_{gp}z_{gp}$. Il fixe ce coût minimal puis minimise un entier $H$ tel que $\sum_{g,p}n_{gpd}z_{gp}\le H$ pour chaque jour. Enfin, à coût et pic fixés, il minimise le nombre total de camions. Les trois preuves d’optimalité portent sur les plans validés du catalogue. Le calcul conserve l’origine de chaque plan de couple dans l’export.

[Notebook moteur](moteur_optimisation.ipynb) · [Code du solveur](../src/delivery_schedule_optimization/optimisation_cout.py) · [Bilan vérifié](../milestone4/bilan_avant_apres.ipynb)
