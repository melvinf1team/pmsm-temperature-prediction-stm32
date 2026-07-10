# PMSM Temperature Prediction STM32

Projet complet de mesure, datalogging et prétraitement pour construire un dataset NanoEdge AI Studio autour d'un moteur PMSM piloté par STM32.

L'objectif est de récupérer les grandeurs moteur et températures depuis une carte STM32 B-G473E-ZEST1S avec power board STDES-LVHP01, puis de préparer un fichier où `d6t_temp_c` est la target d'extrapolation thermique.

## Structure

- `datalogging/` : dashboard Python Tkinter, profils moteur et logs CSV bruts dans `datalogging/logs`.
- `pretraitement/` : script de génération des features et EWMA pour NanoEdge AI Studio.
- `firmware/` : projet STM32CubeIDE/MCSDK et code applicatif embarqué.
- `docs/` : documentation Sphinx locale du projet.
- `requirements.txt` : dépendances Python du dashboard, du prétraitement et de la documentation.

## Installation Python

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Lancer le dashboard

```powershell
python .\datalogging\motor_datalog_gui_dashboard.py
```

Le dashboard permet de sélectionner le port COM, configurer le profil moteur, envoyer `SYNC`, `CFG`, `START` et `STOP`, afficher les mesures en temps réel et écrire les CSV bruts dans `datalogging/logs`.

Colonnes brutes conservées :

```text
stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a
```

## Prétraitement NanoEdge AI

```powershell
python .\pretraitement\preprocess_logs_ewma.py
```

Le script lit par défaut `datalogging/logs/daq_log_*.csv` et écrit les fichiers traités dans `pretraitement/logs_processed_ewma`.

Points importants :

- `d6t_temp_c` reste la première colonne et sert de target NanoEdge AI.
- Aucune EWMA n'est calculée sur `d6t_temp_c`.
- Les spans EWMA de référence `[1320, 3360, 6360, 9480]` sont calibrés pour `2 Hz`.
- Le script déduit la fréquence réelle avec `stm32_time_ms` et adapte les spans automatiquement.
- Les features incluent les mesures instantanées, `u_s`, `i_s`, `S_el`, `speed_current` et `speed_power`.

Options utiles :

```powershell
python .\pretraitement\preprocess_logs_ewma.py --header
python .\pretraitement\preprocess_logs_ewma.py --no-header
python .\pretraitement\preprocess_logs_ewma.py --frequency-hz 10
python .\pretraitement\preprocess_logs_ewma.py --include-time
```

## Firmware STM32

Le firmware est dans `firmware/tets_motor_dewalt`.

Les principaux modules utilisateur sont :

- `app_serial_control.c` : protocole UART textuel et validation des commandes PC.
- `app_motor_control.c` : démarrage, arrêt, configuration moteur et sécurités.
- `app_datalog.c` : envoi du header CSV et des lignes `DATA`.
- `d6t_ir.c` : lecture I2C logiciel du capteur D6T, target `d6t_temp_c`.
- `ds18b20.c` : lecture 1-Wire du capteur DS18B20, feature `ds18b20_temp_c`.

Le protocole série applicatif utilise :

```text
SYNC
CFG,<target_rpm>,<iq_limit_a>,<hard_limit_a>,<accel_elec_hz_s>,<datalog_ms>,<ds18b20_ms>
START
STOP
```

## Documentation

Générer la documentation HTML locale :

```powershell
python -m sphinx -b html .\docs\source .\docs\build\html
```

La documentation décrit l'architecture complète, le dashboard, le prétraitement, le firmware, le câblage et la référence API Python.