# Jalon 4 · Vérification du plan et analyse des gains

Le [notebook avant/après](bilan_avant_apres.ipynb) explique la méthode et exécute la suite de tests. Il contrôle les lignes livrées, les profils, les capacités, les délais et les tarifs, puis compare les coûts et le remplissage sur le périmètre propre à chaque scénario. Il présente aussi la contribution de chaque usine aux économies calculées.

La dernière section contrôle le [plan lissé](resultats_verifies/plan_lisse/) avec profils stricts. Elle compare le coût, le nombre de camions et les pics quotidiens avec le plan de coût minimal sur le même périmètre avec imputation.

Ouvrir le notebook depuis la racine ou depuis `milestone4/`, puis exécuter ses cellules dans l’ordre. Il utilise les [exports figés](resultats_verifies/) et le commit des données `a0e8aba`. Le [guide du projet](../README.md) centralise les règles, les résultats et les indications pour [relancer le moteur](../milestone3/moteur_optimisation.ipynb).

[Suite de tests](test_optimisation_cout.py) · [Annexe mathématique](../milestone3/modelisation_mathematique.md) · [Archives des comparatifs](../archives/milestone4/)
