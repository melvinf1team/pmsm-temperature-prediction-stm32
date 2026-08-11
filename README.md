# PMSM Temperature Prediction STM32

Projet complet de mesure, datalogging et prétraitement pour construire un dataset NanoEdge AI Studio autour d'un moteur PMSM piloté par STM32.

L'objectif est de récupérer les grandeurs moteur et températures depuis une carte STM32 B-G473E-ZEST1S avec power board STDES-LVHP01, puis de préparer un fichier où `d6t_temp_c` est la target d'extrapolation thermique.

## Structure

- `datalogging/` : dashboard Python Tkinter, profils moteur et logs CSV bruts dans `datalogging/logs`.
- `pretraitement/` : script de génération des features et EWMA pour NanoEdge AI Studio.
- `firmware/` : projet STM32CubeIDE/MCSDK et code applicatif embarqué.
- `firmware_validation/` : projet STM32CubeIDE autonome avec prétraitement EWMA temps réel et modèle NanoEdge activable.
- `validation/` : interface graphique D6T face à la prédiction IA en temps réel.
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

Le dashboard propose deux modes : **moteur + collecte** (`SYNC`, `CFG`, `START`) et **collecte seule moteur arrêté** (`SYNC`, `ACQ_START`). Le second ne demande aucun paramètre moteur et permet d'enregistrer le refroidissement après un essai. `STOP` termine proprement les deux types de session.

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
ACQ_START,<datalog_ms>,<ds18b20_ms>
STOP
```

La consigne applicative est bornée à 2500 rpm, 12 A sur `Iq` et 14 A sur le seuil
de courant total. Le bouton B2 de la B-G473E-ZEST1S démarre ou arrête un profil
autonome fixe à 2000 rpm, 10 A `Iq` et 12 A de courant total maximal.

## Firmware de validation NanoEdge

Le projet `firmware_validation/STM32CubeIDE` calcule directement sur la carte
les 55 features produites par `preprocess_logs_ewma.py`. Le choix se fait dans
`firmware_validation/Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U` : le modèle de `firmware_validation/AI_Model/libneai.a` est exécuté et
	l'UART émet `d6t_temp_c;predicted_temp_c` à 10 Hz ;
- `0U` : le modèle n'est pas appelé et l'UART émet les 55 features numériques
	attendues par le Serial Emulator NanoEdge AI Studio.

Le modèle courant est une régression Ridge pour Cortex-M4 hard-float, avec une
entrée `1 x 55`. Pour changer de modèle, remplacer `libneai.a`, `NanoEdgeAI.h`,
`metadata.json` et le dossier `artifacts` dans `firmware_validation/AI_Model`,
puis effectuer un clean build. Le nouvel export doit conserver une entrée de
55 axes et l'API d'extrapolation standard.

Contrôle du port série :

```powershell
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
python .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

La procédure complète et les contraintes de remplacement sont détaillées dans
`firmware_validation/README.md`.

## Interface de validation thermique

Avec `APP_NEAI_MODEL_ENABLED` à `1U`, lancer l'interface qui compare en direct
la température D6T et la prédiction produite dans la carte :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py
```

Connexion automatique ou démonstration sans matériel :

```powershell
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --port COM5
.\.venv\Scripts\python.exe .\validation\test\temperature_validation_gui.py --demo
```

Les deux températures sont affichées en grand avec une décimale. L'interface
présente également l'erreur instantanée et la MAE cumulée de la session, avec
les seuils vert, bleu, orange et rouge, un graphique sur 90 secondes et un
export CSV. Voir `validation/README.md` pour le détail des calculs.

## Documentation

Générer la documentation HTML locale :

```powershell
python -m sphinx -b html .\docs\source .\docs\build\html
```

La documentation décrit l'architecture complète, le dashboard, le prétraitement, le firmware, le câblage et la référence API Python.
