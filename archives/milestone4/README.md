# Archives du jalon 4

Ces fichiers documentent les comparaisons et les essais qui ont précédé la version publiée. Le [guide du projet](../../README.md) pointe vers le moteur, le bilan courant et les résultats vérifiés.

- [Comparatif du plan de Wissam](milestone4_wissam.ipynb) et [chronométrage](chrono_wissam.json), avec le [script de reproduction](chronometrer_wissam.py). Ce comparatif utilise le module Python `holidays` du planificateur d’origine.
- [Bornes indépendantes](borne_calendrier.py), [comparaison de l’ouverture des dates](comparer_anticipation.py), [rechargement exact à dates fixes](recharger_exact.py) et [pilote Hall](pilote_partition_hall.py).
- [Journal détaillé et commandes des essais](https://github.com/exauceh/delivery_schedule_optimization/blob/0f21353/milestone4/README.md). Les commandes du journal correspondent à la structure de ce commit. Dans la structure actuelle, les scripts archivés se lancent depuis la racine avec `src/` dans le `PYTHONPATH`. Certains comparatifs requièrent des sorties d’essais intermédiaires.
