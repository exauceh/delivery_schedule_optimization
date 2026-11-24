#!/bin/bash
# Double-cliquez (macOS) pour ouvrir Gilbert & Co sur cet ordinateur.
cd "$(dirname "$0")"
echo "Installation des composants (la première fois seulement)..."
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo "Gilbert & Co tourne sur http://localhost:8000 — fermez cette fenêtre pour l'arrêter."
python3 main.py
