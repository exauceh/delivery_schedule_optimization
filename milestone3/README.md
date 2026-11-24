# Jalon 3 · Moteur d’optimisation

Le [notebook moteur](moteur_optimisation.ipynb) reproduit par défaut le plan lissé à profils stricts. La première cellule permet aussi de relancer le solveur de coût en mode pilote ou complet. Le [guide du projet](../README.md) décrit la méthode et les fichiers de sortie. Le [jalon 4](../milestone4/) contrôle les plans et chiffre les gains.

L’[annexe mathématique](modelisation_mathematique.md) détaille la fonction objectif, les contraintes et le calcul des bornes. Le [solveur](../src/delivery_schedule_optimization/optimisation_cout.py) et la [consolidation des plans](../src/delivery_schedule_optimization/retenir_meilleurs_couts.py) sont dans `src/`.

## Comparatifs précédents

[Notebook de la variante RG06 stricte](milestone3_prime.ipynb) · [Présentation PDF](../milestone2/planification_testbed/presentation-ensemble.pdf) · [Plan de Wissam](https://github.com/exauceh/delivery_schedule_optimization/blob/a0e8aba/milestone3/candidat_max.ipynb)
