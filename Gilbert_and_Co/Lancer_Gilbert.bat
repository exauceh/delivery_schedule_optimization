@echo off
rem Double-cliquez pour ouvrir Gilbert ^& Co sur cet ordinateur.
cd /d "%~dp0"
echo Installation des composants (la premiere fois seulement)...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo Gilbert ^& Co tourne sur http://localhost:8000 . Fermez cette fenetre pour l arreter.
python main.py
