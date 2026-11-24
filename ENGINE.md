# Moteur de consolidation fusionné (profil A + lissage)

## Lancer

```
pip install pandas openpyxl ortools holidays
python run_consolidation.py data/consolidated_dataset.csv docs/rate_sheet.csv sorties
```

Le moteur part de la sortie du **Milestone 1** (`consolidated_dataset.csv`, règles 1 à 6 déjà appliquées). Il vérifie au départ qu'il retrouve le coût actuel du **Milestone 2** (176 979,32 € avec son estimateur). Pour repartir des fichiers bruts, mettre `consolidated_csv=None` dans `Config`.

Le programme écrit `sortie1_profils.csv`, `sortie2_camions.csv`, `sortie3_saving.csv` et `resultats_consolidation.xlsx`. Le classeur contient les onglets Synthèse, les trois sorties, Lissage, Camions par jour, Préparation, Contrôles et Objectifs. Il s'arrête en erreur si une règle n'est pas respectée.

## Organisation

| Fichier | Rôle |
| :--- | :--- |
| `fusion/config.py` | **Tous** les paramètres : règles de données, capacité, anticipation, pays des fériés, marge, diesel, fréquences, lissage, cibles du prof |
| `fusion/data.py` | Lecture et préparation des données (règles 1 à 9), grille lue dans `rate_sheet.csv`, calendrier |
| `fusion/loading.py` | Chargement des camions : rapide (First-Fit Decreasing) pour la recherche, **exact** (CP-SAT) pour le plan publié |
| `fusion/plans.py` | Plans d'un couple pour un jeu de jours autorisés : heuristique et programmation dynamique |
| `fusion/smoothing.py` | Lissage par usine (CP-SAT, déterministe) |
| `fusion/polish.py` | Réoptimisation exacte de chaque couple, ligne par ligne, après le lissage |
| `fusion/engine.py` | Enchaînement : référence, validation, plans, lissage, amélioration locale, chargement exact, sorties |
| `fusion/report.py` | Contrôles des règles, objectifs du prof, synthèse et fichiers |

## Étapes

1. **Données.** Lecture de `consolidated_dataset.csv` (Milestone 1). Les lignes « camion plein » imputées au M1 sont repérées (134).
2. **Référence et lien avec le M2.** Chaque ligne est livrée le jour de son besoin : 1 665 livraisons. L'estimateur du M2 doit redonner 176 979,32 €, sinon arrêt. Le chargement camion par camion donne 1 741 camions et 176 725 €, et c'est cette référence qui sert au saving (même méthode avant et après).
3. **Validation de départ.** Avec une fenêtre de 0 jour et tous les jours autorisés, le moteur doit retrouver exactement la référence (mêmes livraisons, camions et coût) pour chaque couple. Sinon, il s'arrête.
4. **Plans.** Pour chaque couple et chacun des 31 jeux de jours autorisés, deux sources. Les couples sont traités **en parallèle** sur tous les cœurs (`Config.workers`), et le résultat ne dépend pas de l'ordre :
   - **heuristique** : remplissage des camions par urgence, puis recherche locale ligne par ligne, puis réparation du « profil atteint » ;
   - **programmation dynamique** : meilleur découpage des jours de besoin en groupes livrés ensemble, avec le « profil atteint » imposé dans le calcul.
5. **Lissage par usine (CP-SAT).** On choisit un plan par couple : **coût minimal** : le surcoût autorisé est 0 (`smoothing_max_extra_cost`), donc le pic de camions par jour n'est abaissé que lorsque c'est gratuit ; à coût égal, le moins de camions. La règle 29 est une **contrainte du solveur, usine par usine** : une usine ne reçoit jamais plus de camions après le lissage qu'avant.
6. **Amélioration locale et réoptimisation exacte.** D'abord la recherche locale ci-dessous, puis, pour chaque couple, un modèle CP-SAT ligne par ligne : mêmes jours de livraison, pas plus de camions par jour, semaine de référence du profil conservée. Seul le coût peut baisser, et l'hypothèse « lignes d'un même jour ensemble » de la programmation dynamique disparaît pour ces couples. Recherche locale : Une ligne change de jour de livraison, parmi ceux déjà prévus pour son couple, si le coût baisse sans camion en plus, sans dépasser le pic et sans casser le profil.
7. **Chargement exact.** Chaque livraison qui demande plus d'un camion est rechargée de façon exacte : le moins de camions, puis le coût minimal.
8. **Contrôles et objectifs**, publiés séparément.

## Réponses aux remarques de relecture

| Remarque | Correction |
| :--- | :--- |
| Le changement de profil au lissage était caché | La sortie 1 contient `jours_autorises_avant_lissage` et `profil_change_au_lissage`. L'onglet Lissage et la synthèse donnent le nombre de couples concernés (87). |
| Résultats non reproductibles | CP-SAT utilise un seul processus, une graine fixe et un budget en **temps déterministe** : même entrée = même sortie. Deux exécutions donnent des fichiers identiques octet pour octet. |
| Chargement approché | Le plan publié est rechargé de façon exacte (gain de 16,45 €). La recherche garde le chargement rapide. |
| Hypothèse de la programmation dynamique | Elle est documentée ci-dessous. L'heuristique et l'amélioration locale travaillent ligne par ligne et ne la font pas. |
| Valeurs en dur | Horizon lu dans les données. Grille lue dans `rate_sheet.csv` et vérifiée sur l'exemple 300 km / 1 000 kg = 335,34 €. Règle « ligne > capacité » au lieu d'un poids exact. Fériés obligatoirement fournis par le paquet `holidays`, sans liste de secours. Tout le reste est dans `Config`. |
| Objectifs mélangés aux contrôles | Deux tableaux : **Contrôles** (règles 7 à 29 : le plan est-il correct ?) et **Objectifs** (temps, saving, camions : est-il assez bon ?). Le temps est signalé comme dépendant de la machine. |
| Pas de validation de départ | La validation à fenêtre de 0 jour est faite à chaque exécution (étape 3). |
| Un seul script de 600 lignes | Sept modules courts, une responsabilité chacun, plus ce document. |

## Hypothèses et limites

- **Programmation dynamique.** Elle suppose que les lignes d'un même jour de besoin partent ensemble, et que chaque groupe réunit des jours de besoin consécutifs. Comme le prix d'un envoi croît moins vite que son poids, regrouper ne coûte jamais plus, sauf quand un camion déborde. Ce cas est couvert par l'heuristique et l'amélioration ligne par ligne, mais rien ne garantit l'optimum absolu sur d'autres données.
- **Lissage.** Il choisit parmi les plans générés : le pic obtenu est minimal **parmi ces plans**, pas parmi tous les plans imaginables.
- **Chargement rapide** pendant la recherche : le coût utilisé pour choisir les plans peut dépasser de quelques euros le coût exact publié.
- **Temps.** Environ 55 s sur 2 cœurs. Le solveur du lissage reste à un seul processus, avec un budget déterministe, pour la reproductibilité.

## Résultat sur vos données (identique à chaque exécution)

Réglages : gain maximal (surcoût de lissage 0), premières livraisons le 02/11 (`deliver_before_first_need=True`, premier besoin moins 7 jours d'anticipation).

| Saving | Coût avant → après | Camions | Livraisons | Pics PLT_001 / 002 / 003 / 004 | Temps (2 cœurs) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **67 263,80 € (−38,1 %)** | 176 724,63 € → 109 460,83 € | 754 | 678 | 8 / 31 / 9 / 13 | ≈ 40 s |
