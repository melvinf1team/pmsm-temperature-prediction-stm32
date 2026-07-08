# Datalog PMSM

Projet de datalogging PMSM sur STM32 B-G473E-ZEST1S avec power board STDES-LVHP01.

## Structure

- `pc_dashboard/` : interface Python Tkinter pour contrôle, datalogging CSV et visualisation.
- `firmware/` : projet STM32CubeIDE pour la carte STM32.
- `docs/` : documentation Sphinx du projet.

## Lancer l'interface Python

```powershell
pip install -r requirements.txt
python pc_dashboard/motor_datalog_gui_dashboard.py