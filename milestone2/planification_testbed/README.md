# Testbed de planification des livraisons

## Dossier final fournisseur × usine

Le [notebook explicatif du Milestone 3](../../milestone3/milestone3_prime.ipynb) déroule l’algorithme et vérifie les exports.

**Actualisation : plan à 49 camions au pic.** [Présentation PDF illustrée](presentation-ensemble.pdf) · [Carte de chaleur](heatmap-ensemble-cible.svg) · [Exports du plan à 49](ensemble-cible/).

[Document explicatif, cartes de chaleur et exports finaux](ENSEMBLE_FINAL.md)

Le calcul combine trois sources de calendriers sur les 4 389 lignes préparées de `main` au commit `c069c90`. Il reprend le plan de `main`, le plan publié de Wissam au commit `64e34b9` et notre heuristique, qu’il recalcule à partir des mêmes besoins. Le graphe de Wissam fournit d’autres calendriers. Le moteur essaie les combinaisons de jours déjà observées et explore les 31 combinaisons sur les 40 couples les plus coûteux du plan publié de `main`.

Chaque candidat est rechargé avec le même chargeur et la même grille. Pour RG06 stricte, chaque besoin doit partir au dernier départ admissible du couple à sa date ou avant. Le contrôle RG05 du jalon 3 demande en plus qu’au moins une semaine utilise tous les jours du profil. Le pic global est le premier critère de choix sous le plafond de coût de `main`. À pic égal, le moteur choisit le coût le plus faible. Les plafonds par usine font l’objet d’un scénario distinct.

Depuis la racine du testbed, avec Python, pandas, NumPy, SciPy et holidays, et un dépôt Git contenant les deux commits distants :

```powershell
$env:PYTHONPATH = 'src'
python milestone2/planification_testbed/ensemble_calendriers.py --repo ../delivery_schedule_optimization --output results/ensemble-final
python milestone2/planification_testbed/heatmaps.py --ensemble results/ensemble-final/calendrier.csv
```

L'exécution régénère les candidats heuristiques puis produit le comparatif, les camions, les affectations, les profils et les économies par couple et par usine. Les plans publiés de `main` et de Wissam fournissent les calendriers de départ du comparatif. Le chronomètre ne comprend pas leur résolution d'origine.

| Variante | Coût calculé | Camions | Pic global | Couples sans semaine complète du profil |
|---|---:|---:|---:|---:|
| RG06 stricte, sous le coût de `main` | 110 314,80 € | 733 | 32 | 56 |
| RG05 et RG06, au coût le plus bas dans le vivier élargi | 111 708,13 € | 759 | 53 | 0 |

Les deux variantes couvrent les mêmes lignes et respectent RG06. Le vivier testé ne permet pas de satisfaire également RG05 sous le plafond de 110 329,59 €. La seconde variante dépasse ce plafond de 1 378,54 €. Elle reste sous 800 camions et présente 65 016,48 € d’économies calculées par rapport à la référence reconstruite. Aucun contrôle de la préférence de fréquence RG07 n’est déduit de ces résultats.

### Expérience sur les jours de pointe

L’option `--enrich-peaks` teste les 31 profils fixes pour chacun des 180 couples. À partir du premier plan RG05 et RG06, elle repère les trois dates les plus chargées. Pour les principaux contributeurs à ces pointes, elle demande aussi au graphe un calendrier qui évite cette date. Les nouveaux chemins passent les mêmes contrôles de dates et de chargement. Le comparatif affiche le résultat avant et après cet ajout ciblé.

```powershell
python milestone2/planification_testbed/ensemble_calendriers.py --repo ../delivery_schedule_optimization --output results/ensemble-enrichi --enrich-peaks
```

Ajouter `--profile-pairs 80` pour explorer tous les profils sur les 80 couples les plus coûteux et conserver les profils déjà observés sur les autres. Cette variante garde le ciblage des trois dates de pointe.

| Recherche RG05 et RG06 | Coût | Camions | Pic quotidien | Temps du processus |
|---|---:|---:|---:|---:|
| Dossier publié, avant ciblage | 111 708,13 € | 759 | 53 | — |
| 80 couples avec ciblage | 111 618,27 € | 759 | 49 | 85,03 s |
| 180 couples avec ciblage | 111 549,39 € | 758 | 48 | 117,08 s |

Le ciblage fait passer le pic de 52 à 49 sur le vivier à 80 couples et de 51 à 48 sur le vivier complet, au même coût pour chaque vivier. Le plafond de 110 329,59 € reste hors d’atteinte avec ces options. Le chronomètre inclut la génération de l’heuristique et l’export, et réutilise les calendriers publiés de `main` et de Wissam.

Le dossier `ensemble-cible/` contient le comparatif, le calendrier, les camions, les affectations et les économies du plan à 49. Depuis le dépôt, la présentation se régénère à partir de ce dossier avec `python milestone2/planification_testbed/make_ensemble_pdf.py`, en installant ReportLab dans l’environnement Python utilisé.

## Plan mixte fournisseur × usine

Sur les **4 389 lignes préparées** du jalon 3 de `main` (`db269fc`), le plan mixte réduit le pic de **77 à 44 camions par jour** et coûte **59,92 € de moins** que le plan `main`. Il couvre les mêmes besoins et conserve les imputations du jeu préparé, identifiées dans les affectations.

![Carte de chaleur des camions quotidiens sur une échelle commune](heatmap-lissage.svg)

| Scénario | Coût calculé | Camions | Pic par jour |
|---|---:|---:|---:|
| Référence quotidienne reconstruite | 176 724,61 € | 1 741 | 118 |
| Moteur de `main` | 107 367,96 € | 702 | 77 |
| Heuristique seule | 108 637,92 € | 694 | 54 |
| **Plan mixte sans surcoût** | **107 308,04 €** | **696** | **44** |
| Plan mixte au pic minimal | 107 384,41 € | 697 | 43 |

Pour chaque couple, le calcul choisit l'un des deux plans existants et conserve toutes ses affectations. Il minimise le pic de camions sur le calendrier global sous un plafond de coût, puis cherche le coût le plus bas au pic atteint. Le plan retenu reprend l'heuristique pour 48 des 180 couples. Le pic de 43 est le plus bas dans cet ensemble de plans et coûte 76,37 € de plus que le plan à 44.

Le plan à 44 présente **69 416,57 € d’économies calculées** par rapport à la référence reconstruite. Le run local vérifie la couverture, les charges, les capacités et les dates d’affectation. Il reconstruit l’heuristique et combine ses résultats avec le plan solveur déjà publié par `main`.

[Comparatif et budgets](comparatif-lissage.csv) · [Camions du plan mixte](plan-mixte/camions.csv) · [Affectations du plan mixte](plan-mixte/affectations.csv)

### Reproduire le lissage

Depuis la racine du dépôt, avec Python, pandas, NumPy et SciPy. Récupérer aussi l'historique de `main` si le clone ne contient que `prime`. La révision du jalon 3 est fixée à `db269fc`.

```powershell
git fetch origin main
$env:PYTHONPATH = 'src'
python milestone2/planification_testbed/lissage_duel.py
python milestone2/planification_testbed/heatmaps.py --lissage results/duel-db269fc/lissage/calendrier.csv
```

Les résultats détaillés et le chronomètre sont dans `results/duel-db269fc/lissage/`.

## Testbed hub initial sur charges et distances d'origine

Le testbed du Milestone 2 compare une référence quotidienne reconstruite, des profils fournisseur × usine et des profils fournisseur → hub sur les mêmes 4 251 lignes à charge et distance d'origine. Le [document pas à pas](../ALGORITHME_PLANIFICATION.md) explique les règles, le tarif, la construction des plans et leur validation.

Le [comparatif de référence](comparatif.csv) contient le résultat local. Le scénario hub utilise 338 camions et présente un écart calculé de 86 558,67 € avec la référence. Le processus complet a pris 10,55 secondes sur le poste de test.

La [carte de chaleur des camions par jour](heatmap-camions.svg) compare A, B et C sur un calendrier et une échelle communs. Pour la régénérer après le run, exécuter `python milestone2/planification_testbed/heatmaps.py`.

Ce run s'appuie sur les entrées de `79d34ff`, notamment `data/schedule_clean.csv`. Le `main` plus récent emploie une autre préparation et a retiré ce fichier. Les chiffres de cette publication décrivent le jeu figé sur `prime`.

## Reproduire

Depuis la racine du dépôt avec Python, pandas et NumPy, par exemple dans PowerShell.

```powershell
$env:PYTHONPATH = 'src'
python -m delivery_schedule_optimization.fast_plan --output results/fast_plan
```

Le [point d'entrée Python](../../src/delivery_schedule_optimization/fast_plan.py) vérifie la couverture, les capacités et les dates avant d'exporter les affectations, les camions et le bilan. Les affectations détaillées sont générées à l'exécution.
