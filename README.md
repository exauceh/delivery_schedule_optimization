# Optimisation du planning de livraison

Le moteur choisit les jours de départ et les chargements qui minimisent le coût du transport par couple fournisseur × usine. Il utilise les besoins du jeu préparé, avec deux périmètres de calcul selon que les données manquantes sont imputées ou exclues du chiffrage.

## Où trouver quoi

| Besoin | Fichier | Contenu |
|---|---|---|
| **Calculer un plan** | [Jalon 3 · Notebook moteur](milestone3/moteur_optimisation.ipynb) | Exécute le solveur en mode pilote ou complet et produit les fichiers de sortie. |
| **Vérifier le plan et expliquer les gains** | [Jalon 4 · Notebook avant/après](milestone4/bilan_avant_apres.ipynb) | Tests, exhaustivité, capacités, délais, coûts, remplissage et contributions par usine. |
| **Consulter le plan publié** | [Synthèse du calcul](milestone4/resultats_verifies/comparatif.json) | Meilleur plan vérifié, économies calculées et écart restant à la borne. |
| **Relire l’implémentation** | [Solveur Python](milestone4/optimisation_cout.py) · [Suite de tests](milestone4/test_partition_hall.py) | Le notebook moteur appelle ce code. |

## Comment fonctionne le moteur

1. **Préparer le problème.** Une ligne de besoin reste entière. Son poids et son volume sont des totaux de ligne. Le scénario avec imputation reprend les valeurs du jeu préparé. Le scénario sans imputation chiffrable garde les lignes dont le poids, le volume et la distance sont connus.
2. **Définir les dates possibles.** Une ligne peut partir sur un jour ouvré compris entre J−7 et J. L’horizon commence le 2 novembre 2026. Un profil est un ensemble de jours ouvrés qui doit être réalisé au moins une fois dans une même semaine. Une exception permet de couvrir les besoins trop précoces pour le premier départ régulier.
3. **Optimiser dates et chargements ensemble.** Chaque ligne est affectée à un camion. Un camion est limité à 24 500 kg et 110,4 m³. L’objectif est le coût total selon la grille poids-distance, appliquée camion par camion. À 126 km, séparer 50 kg et 1 000 kg donne un tarif de base total de 49,27 €, contre 51,20 € ensemble.
4. **Vérifier et chiffrer.** Le moteur garde le meilleur plan faisable et cherche une borne inférieure. Le contrôle rapproche l’intégralité des lignes du périmètre avec les camions, vérifie les dates et les capacités, puis recalcule les coûts. La référence reconstruit les transports aux dates de besoin sur les mêmes lignes.

Les couples sont indépendants dans ce modèle. Le solveur peut examiner les 31 profils de jours ouvrés en parallèle. Il conserve les meilleures bornes disponibles par couple. Les comparaisons utilisent des tarifs calculés et une référence reconstruite.

## Résultats vérifiés

| Périmètre | Lignes | Coût de référence → coût du plan | Camions avant → après | Économies calculées | Écart à la borne |
|---|---:|---:|---:|---:|---:|
| Avec imputation | 4 389 | 176 724,61 € → 109 345,78 € | 1 741 → 756 | 67 378,83 € · 38,13 % | 1 677,09 € |
| Sans imputation, chiffrable | 4 251 | 142 964,05 € → 75 895,85 € | 1 603 → 627 | 67 068,20 € · 46,91 % | 1 498,05 € |

Le coût minimal est certifié pour 163 couples sur 180 avec imputation et 156 sur 171 sans imputation. L’écart à la borne plafonne ce qui pourrait encore être économisé dans le modèle. Les économies de chaque ligne du tableau s’interprètent par rapport à sa propre référence.

## Exports

- [Profils et coûts par couple](milestone4/resultats_verifies/couples.csv)
- [Camions et charges totales](milestone4/resultats_verifies/camions.csv)
- [Affectations, articles et quais](milestone4/resultats_verifies/affectations.csv)
- [Registre des profils examinés sur les cas encore ouverts](milestone4/resultats_verifies/profils.csv)

## Rejouer le calcul

Utiliser un environnement Jupyter avec Python, pandas, NumPy, SciPy et holidays. Si le commit des données `a0e8aba` manque dans le clone, lancer `git fetch origin milestone3-candidat-wissam`. Ouvrir le notebook moteur depuis la racine ou depuis `milestone3/`, puis exécuter ses cellules dans l’ordre.

Le mode `pilote` résout un couple dans les deux périmètres. Le mode `complet` relance tous les couples à partir du plan figé et consolide le meilleur plan faisable et la meilleure borne disponibles. Les nouvelles sorties vont dans `results/moteur-notebook-pilote/` ou `results/moteur-notebook-complet/`. Le notebook du jalon 4 audite les exports publiés dans `milestone4/resultats_verifies/`. Un calcul limité en temps peut produire un plan ou une borne différents.

Les comparatifs précédents restent disponibles dans les dossiers des [jalons 3](milestone3/) et [4](milestone4/).
