Firmware STM32
==============

Organisation
------------

Le firmware se trouve dans ``firmware/tets_motor_dewalt``. Il combine un projet
STM32CubeIDE/MCSDK généré et plusieurs modules utilisateur situés dans
``STM32CubeIDE/Application/User`` et déclarés dans ``Inc``.

Les modules applicatifs principaux sont :

``app_serial_control.c``
   Réception UART, parsing des commandes PC, validation des paramètres et envoi
   des ``ACK`` / ``ERR``.

``app_motor_control.c``
   Démarrage, arrêt, configuration runtime du moteur et sécurités courant /
   survitesse.

``app_datalog.c``
   Prise en main de l'USART, file TX non bloquante, envoi du header CSV et des
   lignes ``DATA``.

``d6t_ir.c``
   Lecture I2C logiciel du capteur IR D6T et formatage de ``d6t_temp_c``.

``ds18b20.c``
   Driver 1-Wire du DS18B20 avec cache de dernière valeur valide.

Initialisation
--------------

Dans ``Src/main.c``, l'ordre applicatif est :

.. code-block:: c

   AppDatalog_Init();
   AppMotorControl_Init();
   AppSerialControl_Init();

Puis la boucle principale appelle :

.. code-block:: c

   AppSerialControl_Task();
   AppMotorControl_Task();
   AppDatalog_Task();

``AppDatalog_Init`` désactive l'usage ASPEP/DMA de ``USART1`` pour laisser la
place au protocole ASCII du projet. Les messages de boot indiquent l'état du
logger et du capteur D6T.

Contrôle série
--------------

``AppSerialControl_OnUsart1Irq`` lit les octets reçus dans une file circulaire.
``AppSerialControl_Task`` reconstruit les lignes ASCII et cherche une commande
connue même si des octets parasites précèdent la commande.

``CFG`` est validé par bornes : vitesse cible, limite ``Iq``, hard stop,
accélération, période DATA et période DS18B20. Une configuration valide appelle
``AppMotorControl_SetRuntimeConfig`` et ``AppDatalog_SetRuntimePeriods``.
``ACQ_START`` valide uniquement les deux périodes, force le moteur à l'arrêt et
arme le logger sans exiger de configuration moteur.

Contrôle moteur
---------------

``AppMotorControl_Start`` applique la configuration runtime, ajuste le PI vitesse
et démarre le moteur via MCSDK avec polarisation. La limite ``Iq`` est rampée en
RUN pour éviter une demande de couple brutale. ``AppMotorControl_Task`` surveille
les faults MCSDK, le dépassement de courant et la survitesse.

Le bouton B2 sur ``PC13`` dépose une requête dans l'interruption, puis la boucle
principale applique le profil autonome 2000 rpm / 10 A ``Iq`` / 12 A total. Un
second appui arrête le moteur. Le traitement différé et un anti-rebond de 250 ms
évitent d'appeler le MCSDK directement depuis l'interruption.

Datalogging embarqué
--------------------

``AppDatalog_StartLogging`` arme le logger après réception de ``START``. Le
header envoyé est :

.. code-block:: text

   #CSV_HEADER,stm32_time_ms,d6t_temp_c,ds18b20_temp_c,motor_ud_v,motor_uq_v,motor_speed_mech_rpm,motor_id_a,motor_iq_a

Chaque ligne ``DATA`` contient le tick STM32, les températures, les tensions d/q
reconstruites, la vitesse mécanique et les courants d/q. Les tensions d/q sont
calculées depuis ``CurrCtrl_M1.Ddq_out_pu`` et la tension bus DC. Hors état RUN,
les grandeurs moteur sont forcées à zéro pour éviter d'enregistrer les dernières
valeurs mémorisées par le MCSDK.

Capteur D6T
-----------

``d6t_ir.c`` utilise un I2C logiciel sur ``PB6``/``PB9``, exposés respectivement
sur ``CN10-27`` et ``CN10-24``. Le module lit un frame de 35 octets, vérifie le
PEC et extrait le pixel ``D6TIR_SELECTED_PIXEL_INDEX``. La valeur est formatée
en degrés Celsius avec une décimale. Tant qu'aucune lecture valide n'existe,
``D6TIR_GetCsvValue`` renvoie ``NaN``.

Capteur DS18B20
---------------

``ds18b20.c`` pilote le bus 1-Wire avec des fenêtres critiques très courtes pour
ne pas perturber le contrôle moteur. La conversion 12 bits dure 750 ms. Si une
lecture fraîche échoue mais qu'une ancienne valeur valide existe, le driver
renvoie la dernière valeur connue afin de garder un CSV exploitable.

Sécurités
---------

Les sécurités sont réparties entre PC et firmware. Le dashboard valide les
entrées utilisateur pour guider l'opérateur. Le firmware garde les bornes finales
de 2500 rpm, 12 A ``Iq`` et 14 A total, puis coupe le moteur en cas de fault
MCSDK, courant trop élevé ou survitesse. Les sources Workbench et les fichiers C
générés utilisent tous une limite applicative de 12 A afin qu'une régénération ne
réintroduise pas l'ancien plafond de 5 A.

Firmware de validation IA
-------------------------

``firmware_validation`` est un second projet autonome, simplifié pour la
validation NanoEdge. Il ne contient plus le protocole de commandes du dashboard
ni le module de debug ASCII. Le flux UART démarre automatiquement et son format
dépend uniquement de ``APP_NEAI_MODEL_ENABLED`` : deux températures lorsque le
modèle est actif, ou les 55 features lorsque le modèle est inactif.

La bibliothèque est stockée dans ``firmware_validation/AI_Model`` et liée par
les configurations Debug et Release. ``app_ai_model.c`` vérifie à la compilation
que le header annonce un signal de longueur 1 et 55 axes, puis contrôle encore
les dimensions retournées par la bibliothèque avant son initialisation.

Le bouton B2 dépose une intention de démarrage persistante dans la machine
d'états applicative. Si MCSDK est encore dans ``STOP`` ou ``FAULT_OVER``, cette
intention attend le retour réel à ``IDLE`` au lieu d'être perdue. Les faults
terminés sont acquittés et le démarrage est retenté à cadence limitée, sans
attente bloquante. L'ordre ``MC_StopMotor1`` n'est émis qu'une fois à l'entrée
d'un défaut afin de laisser MCSDK atteindre son état acquittable.

Le contexte des 44 EWMA est sauvegardé après chaque échantillon dans deux
snapshots alternés de la section SRAM ``.noinit``. Une signature, une version,
une séquence et un CRC32 permettent de restaurer le dernier snapshot complet
après un reset CPU/NRST tant que la carte reste alimentée. Une coupure
d'alimentation ou un snapshot incohérent provoque une réinitialisation propre du
contexte. Cette stratégie n'écrit pas dans la Flash.

Enfin, la pompe USART1 réactive le périphérique si nécessaire et purge les
drapeaux ``ORE``, ``FE`` et ``NE`` avant de continuer la file TX non bloquante.
