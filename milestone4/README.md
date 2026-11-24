# Milestone 4 · Vérification du plan et des gains

Le [notebook du Milestone 4](milestone4_wissam.ipynb) utilise le plan retenu de Wissam au commit a0e8aba. Il rapproche d’abord les lignes et les camions, puis reconstruit le coût de référence et compare les coûts et le remplissage.

Depuis le dépôt, exécuter `git fetch origin` pour récupérer les objets Git de la branche de Wissam. Ouvrir le notebook avec un environnement qui contient pandas, NumPy, SciPy et holidays. Les résultats de la vérification sont enregistrés dans le notebook.

## Résultat vérifié

**760 camions, 66 180,66 € d’économies calculées et 46,11 s de bout en bout** sur le poste de test. Les affectations recalculées correspondent à l’export de Wissam. Le pic passe de 118 à 68 camions dans la journée la plus chargée et les pics baissent dans les quatre usines. [Détail des mesures](chrono_wissam.json)

La couverture s’applique aux 4 389 lignes préparées. L’audit relève 134 lignes dont une charge est imputée, 379 besoins du 1er janvier hors horizon et une ligne de 26,9 t écartée avant la planification. RG06 assouplie autorise les 142 affectations qui s’écartent de la lecture stricte.

Les 150 couples dont toutes les charges et distances sont d’origine représentent 55 097,58 € des économies calculées. Les 30 autres représentent 11 083,08 €. Cette ventilation décrit le plan publié, sans isoler l’effet de l’imputation.

Reproduire le scénario retenu seul depuis la racine du dépôt.

```powershell
python milestone4/chronometrer_wissam.py --workers 8
```

Le chronomètre inclut la lecture des objets Git locaux, la reconstruction de la référence, la génération des candidats, le lissage, les contrôles et les exports dans `results/milestone4-wissam/`.
