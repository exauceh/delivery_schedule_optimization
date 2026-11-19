# Testbed de planification fournisseur → hub

Le testbed du Milestone 2 compare une référence quotidienne reconstruite, des profils fournisseur × usine et des profils fournisseur → hub sur les mêmes 4 251 lignes à charge et distance d'origine. Le [document pas à pas](../ALGORITHME_PLANIFICATION.md) explique les règles, le tarif, la construction des plans et leur validation.

Le [comparatif de référence](comparatif.csv) contient le résultat local. Le scénario hub utilise 338 camions et présente un écart calculé de 86 558,67 € avec la référence. Le processus complet a pris 10,55 secondes sur le poste de test.

Ce run s'appuie sur les entrées de `79d34ff`, notamment `data/schedule_clean.csv`. Le `main` plus récent emploie une autre préparation et a retiré ce fichier. Les chiffres de cette publication décrivent le jeu figé sur `prime`.

## Reproduire

Depuis la racine du dépôt avec Python, pandas et NumPy, par exemple dans PowerShell.

```powershell
$env:PYTHONPATH = 'src'
python -m delivery_schedule_optimization.fast_plan --output results/fast_plan
```

Le [point d'entrée Python](../../src/delivery_schedule_optimization/fast_plan.py) vérifie la couverture, les capacités et les dates avant d'exporter les affectations, les camions et le bilan. Les affectations détaillées sont générées à l'exécution.
