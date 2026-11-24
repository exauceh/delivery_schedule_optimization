# Optimisation du planning de livraison

Le moteur choisit les jours de départ et les chargements qui minimisent le coût du transport par couple fournisseur × usine. Il utilise les besoins du jeu préparé, avec deux périmètres de calcul selon que les données manquantes sont imputées ou exclues du chiffrage.

## Où trouver quoi

| Besoin | Fichier | Contenu |
|---|---|---|
| **Calculer un plan** | [Jalon 3 · Notebook moteur](milestone3/moteur_optimisation.ipynb) | Reproduit le plan lissé ou relance la génération de plans en mode pilote ou complet. |
| **Comprendre la formulation** | [Jalon 3 · Annexe mathématique](milestone3/modelisation_mathematique.md) | Variables, objectif, contraintes, relaxation et preuve de coût. |
| **Vérifier le plan et expliquer les gains** | [Jalon 4 · Notebook avant/après](milestone4/bilan_avant_apres.ipynb) | Tests, exhaustivité, capacités, délais, coûts, remplissage et contributions par usine. |
| **Consulter les plans publiés** | [Plan lissé](milestone4/resultats_verifies/plan_lisse/) · [Synthèse du moteur de coût](milestone4/resultats_verifies/comparatif.json) | Compromis coût et réception, plans de coût minimal et bornes. |
| **Relire l’implémentation** | [Solveur de coût](src/delivery_schedule_optimization/optimisation_cout.py) · [Solveur de lissage](src/delivery_schedule_optimization/lissage_publie.py) · [Tests](milestone4/test_optimisation_cout.py) | Le notebook moteur appelle les solveurs et le jalon 4 exécute les tests. |

## Comment fonctionne le moteur

1. **Préparer le problème.** Une ligne de besoin reste entière. Son poids et son volume sont des totaux de ligne. Le scénario avec imputation reprend les valeurs du jeu préparé. Le scénario sans imputation chiffrable garde les lignes dont le poids, le volume et la distance sont connus.
2. **Définir les dates possibles.** Une ligne peut partir sur un jour ouvré compris entre J−7 et J. L’horizon commence le 2 novembre 2026. Un profil est un ensemble de jours ouvrés qui doit être réalisé au moins une fois dans une même semaine. Une exception permet de couvrir les besoins trop précoces pour le premier départ régulier.
3. **Optimiser dates et chargements ensemble.** Chaque ligne est affectée à un camion. Un camion est limité à 24 500 kg et 110,4 m³. L’objectif est le coût total selon la grille poids-distance, appliquée camion par camion. À 126 km, séparer 50 kg et 1 000 kg donne un tarif de base total de 49,27 €, contre 51,20 € ensemble.
4. **Vérifier et chiffrer.** Le moteur garde le meilleur plan faisable et cherche une borne inférieure. Le contrôle rapproche l’intégralité des lignes du périmètre avec les camions, vérifie les dates et les capacités, puis recalcule les coûts. La référence reconstruit les transports aux dates de besoin sur les mêmes lignes.

La génération des plans traite les couples indépendamment et peut examiner les 31 profils de jours ouvrés en parallèle. La sélection du plan lissé relie ensuite les couples d’une même usine par un plafond quotidien. Les comparaisons utilisent des tarifs calculés et une référence reconstruite.

## Résultats vérifiés

### Plan lissé à profils stricts · 4 389 lignes avec imputation

| Plan avec imputation | Coût calculé | Camions | Pic global par jour | Pics PLT_001 / PLT_002 / PLT_003 / PLT_004 |
|---|---:|---:|---:|---|
| Meilleur plan de coût publié | 109 345,78 € | 756 | 62 | 14 / 37 / 12 / 19 |
| **Plan lissé** | **109 365,68 €** | **755** | **43** | **8 / 31 / 9 / 13** |

Le plan lissé économise **67 358,93 €** sur la référence reconstruite. Son surcoût face au plan de coût minimal est de **19,89 €** avant arrondi des totaux. Les jours de départ suivent tous les profils. Sur les 1 205 plans candidats validés, le solveur minimise le coût sous les plafonds observés dans le plan d’Arianne, puis le pic global et enfin le nombre de camions. Les commits sources sont dans le [bilan du plan lissé](milestone4/resultats_verifies/plan_lisse/comparatif.json).

### Plans du moteur de coût et bornes

| Périmètre | Lignes | Coût de référence → coût du plan | Camions avant → après | Économies calculées | Écart à la borne |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 4 389 | 176 724,61 € → 109 345,78 € | 1 741 → 756 | 67 378,83 € · 38,13 % | 1 677,09 € |
| Sans imputation, chiffrable | 4 251 | 142 964,05 € → 75 895,85 € | 1 603 → 627 | 67 068,20 € · 46,91 % | 1 498,05 € |

Le coût minimal est certifié pour 163 couples sur 180 avec imputation et 156 sur 171 sans imputation. L’écart à la borne plafonne ce qui pourrait encore être économisé dans le modèle. Les économies de chaque ligne du tableau s’interprètent par rapport à sa propre référence.

## Exports

- Plan lissé · [Profils et coûts par couple](milestone4/resultats_verifies/plan_lisse/couples.csv) · [Camions](milestone4/resultats_verifies/plan_lisse/camions.csv) · [Affectations](milestone4/resultats_verifies/plan_lisse/affectations.csv)
- [Profils et coûts par couple](milestone4/resultats_verifies/couples.csv)
- [Camions et charges totales](milestone4/resultats_verifies/camions.csv)
- [Affectations, articles et quais](milestone4/resultats_verifies/affectations.csv)
- [Registre des profils examinés sur les cas encore ouverts](milestone4/resultats_verifies/profils.csv)

## Rejouer le calcul

Utiliser un environnement Jupyter avec Python, pandas, NumPy, SciPy et holidays. Si le commit des données `a0e8aba` manque dans le clone, lancer `git fetch origin milestone3-candidat-wissam`. Ouvrir le notebook moteur depuis la racine ou depuis `milestone3/`, puis exécuter ses cellules dans l’ordre.

Le mode `lisse` utilise les exports de sept versions Git dont les commits sont figés dans le bilan. Utiliser un clone complet et lancer `git fetch origin` pour récupérer les branches sources. Le mode `pilote` résout un couple dans les deux périmètres. Le mode `complet` relance tous les couples à partir du plan figé et consolide le meilleur plan faisable et la meilleure borne disponibles. Chaque mode écrit dans `results/moteur-notebook-<mode>/`. Le notebook du jalon 4 audite les exports publiés dans `milestone4/resultats_verifies/`. Un calcul limité en temps peut produire un plan ou une borne différents.

Les comparatifs précédents restent disponibles au [jalon 3](milestone3/) et dans les [archives du jalon 4](archives/milestone4/).
