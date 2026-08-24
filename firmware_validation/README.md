# Firmware de validation NanoEdge AI

Ce projet STM32CubeIDE autonome reprend la commande moteur de
`tets_motor_dewalt`, calcule en direct le meme pretraitement EWMA que
`pretraitement/preprocess_logs_ewma.py` et propose deux modes de fonctionnement.
Le choix se fait avec une seule constante dans `Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  1U
```

- `1U` : modele NanoEdge embarque, sortie `temperature D6T;prediction`.
- `0U` : modele non appele, sortie des 55 features pour le Serial Emulator.

Le flux demarre automatiquement au boot. Il ne contient ni header, ni prefixe
`DATA`, ni message de diagnostic. USART1 est configure en 115200 bauds, 8N1.

## Mode modele active

Le modele integre se trouve dans `AI_Model` :

```text
AI_Model/
|-- libneai.a
|-- NanoEdgeAI.h
|-- metadata.json
`-- artifacts/
    |-- ridge_model_params.json
    |-- ridge_preprocessing_config.json
    `-- ridge_preprocessing_params.json
```

Le modele courant est une regression Ridge NanoEdge AI Studio 5.2 :

- ID : `6a79cf7c7fbdd7bdafe35c6c` ;
- cible : STM32G4 Cortex-M4, hard-float ;
- entree : un echantillon de 55 axes ;
- API : `neai_extrapolation_init()` puis `neai_extrapolation()` ;
- score NanoEdge : `0.983` ;
- KPI principal R2 annonce : `0.9961`.

Au demarrage, le firmware verifie les dimensions annoncees par la bibliotheque,
initialise le modele, puis lui transmet le vecteur de 55 floats. Une copie est
utilisee car l'API NanoEdge accepte un buffer mutable.

Chaque ligne UART contient deux nombres en degres Celsius :

```text
<d6t_temp_c>;<predicted_temp_c>
```

Exemple :

```text
31.400000;30.872314
```

Aucune ligne n'est emise tant que le D6T n'a pas fourni une mesure valide ou si
l'initialisation/inference NanoEdge echoue. Le bouton bleu `B2` conserve le
profil moteur autonome : premier appui pour demarrer, second appui pour arreter.

L'appui B2 est memorise jusqu'a ce que la machine d'etats MCSDK soit reellement
revenue dans `IDLE`. Apres un etat transitoire `STOP` ou `FAULT_OVER`, le
firmware acquitte le fault puis retente le demarrage toutes les 100 ms sans
bloquer la boucle principale. Un nouvel appui annule une demande encore en
attente. Les securites courant, survitesse et timeout restent prioritaires.

La pompe USART1 verifie aussi l'etat du peripherique et efface les erreurs
`ORE`, `FE` et `NE` avant de poursuivre la file TX. Une deconnexion physique du
ST-Link/VCP reste du ressort du PC et du cable, mais une erreur UART locale ne
necessite plus de reset applicatif.

Verifier le flux :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode model
```

## Mode Serial Emulator

Mettre dans `Inc/app_config.h` :

```c
#define APP_NEAI_MODEL_ENABLED  0U
```

Faire ensuite un `Project > Clean`, puis reconstruire et reflasher. La
bibliotheque n'est plus appelee et aucun symbole d'inference n'est extrait dans
l'ELF. Chaque ligne contient exactement les 55 features numeriques separees par
`;`, soit les colonnes 2 a 56 des CSV preprocesses. La cible D6T, presente dans
la premiere colonne des fichiers d'entrainement, est volontairement absente a
l'inference.

```text
27.180000;27.180000;...;31385.884088
```

Dans NanoEdge AI Studio, choisir `Validation > Serial Emulator`, le port COM de
la carte et 115200 bauds. Aucune commande `START` n'est necessaire.

Verifier le flux :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\check_nanoedge_serial.py --port COM5 --mode emulator
```

## Contrat du pretraitement

La cadence est 10 Hz, soit une periode de 100 ms. Les logs d'entrainement ont
utilise les spans suivants :

```text
6600, 16800, 31800, 47400
```

Les 11 signaux sont ordonnes ainsi :

1. `ds18b20_temp_c`
2. `motor_ud_v`
3. `motor_uq_v`
4. `motor_speed_mech_rpm`
5. `motor_id_a`
6. `motor_iq_a`
7. `u_s = sqrt(ud^2 + uq^2)`
8. `i_s = sqrt(id^2 + iq^2)`
9. `S_el = 1.5 * u_s * i_s`
10. `speed_current = motor_speed_mech_rpm * i_s`
11. `speed_power = motor_speed_mech_rpm * S_el`

Pour chaque signal, le vecteur contient la valeur instantanee, puis ses quatre
EWMA. Le total est donc de 55 floats. La recurrence reproduit
`pandas.Series.ewm(span=..., adjust=False)` et les valeurs non finies des
features sont remplacees par zero comme dans le script Python.

### Persistance lors d'un reset

Deux snapshots alternes du contexte EWMA sont conserves dans la section SRAM
`.noinit`. Chaque snapshot contient une version, une sequence et un CRC32 ; il
n'est marque valide qu'apres la copie complete. Au boot, le snapshot valide le
plus recent est restaure. Un reset CPU, un appui sur Reset ou un reset depuis le
debugger conserve donc l'historique tant que la SRAM reste alimentee.

Une coupure d'alimentation peut effacer la SRAM. Dans ce cas, ou si le CRC/la
version ne correspond pas, le firmware repart automatiquement avec un EWMA
neuf. Aucune ecriture Flash periodique n'est utilisee, donc ce mecanisme
n'entraine aucune usure de la Flash.

Verifier la parite embarque/Python :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_preprocess_parity.py
```

Le test couvre 72 820 lignes. L'erreur relative mise a l'echelle maximale
mesuree entre float32 embarque et pandas float64 est `0.000445805`.

## Remplacer le modele NanoEdge

Exporter une nouvelle bibliotheque d'extrapolation depuis NanoEdge AI Studio,
puis remplacer dans `firmware_validation/AI_Model` :

1. `libneai.a` ;
2. `NanoEdgeAI.h` ;
3. `metadata.json` ;
4. le contenu de `artifacts/` pour garder la tracabilite du modele.

Vider d'abord `artifacts/` afin de ne pas conserver les JSON de l'ancien
algorithme. Ne pas remplacer `feature_order.txt` : ce fichier decrit l'ordre
impose par le pretraitement embarque et sert au controle de compatibilite.

Le nouvel export doit respecter toutes les contraintes suivantes :

- cible Cortex-M4 STM32G4 compatible avec la carte ;
- ABI flottante `hard` et VFPv4-D16 ;
- `NEAI_INPUT_SIGNAL_LENGTH == 1` ;
- `NEAI_INPUT_AXIS_NUMBER == 55` ;
- bibliotheque d'extrapolation exposant `neai_extrapolation_init` et
  `neai_extrapolation` sans suffixe multi-library.

Les assertions de `app_ai_model.c` font echouer la compilation si les dimensions
changent. Si NanoEdge exporte des fonctions suffixees en mode multi-library, il
faut adapter leurs deux noms dans `app_ai_model.c`.

Verifier le contenu copie avant de reconstruire :

```powershell
.\.venv\Scripts\python.exe .\firmware_validation\tests\validate_neai_export.py
```

Apres remplacement :

1. supprimer le contenu des dossiers `STM32CubeIDE/Debug` et
   `STM32CubeIDE/Release` via `Project > Clean` ;
2. reconstruire le projet ;
3. verifier que `libneai.a` apparait sur la ligne de lien ;
4. reflasher `STM32CubeIDE/Debug/firmware_validation.elf` ;
5. controler la sortie avec `check_nanoedge_serial.py --mode model`.

## Construction

Importer `firmware_validation/STM32CubeIDE` comme projet existant dans
STM32CubeIDE. Les configurations Debug et Release produisent toutes deux
`firmware_validation.elf`.

La bibliotheque et son header sont deja declares dans `.cproject` :

- include : `../../AI_Model` ;
- chemin de bibliotheque : `../../AI_Model` ;
- bibliotheque : `:libneai.a`.

Le firmware ne depend d'aucun paquet Python pour s'executer. Les scripts de test
utilisent uniquement les dependances deja presentes dans `requirements.txt`.
