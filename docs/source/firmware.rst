Firmware STM32
==============

Organisation
------------

Le firmware d'acquisition se trouve dans
``firmware_acquisition/tets_motor_dewalt``. Il combine un projet
STM32CubeIDE/MCSDK généré et plusieurs modules utilisateur situés dans
``STM32CubeIDE/Application/User`` et déclarés dans ``Inc``. Le projet à importer
dans STM32CubeIDE est le sous-dossier ``STM32CubeIDE``.

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

Les bornes du protocole sont 100 à 4500 rpm, 30 A maximum sur ``Iq`` et le hard
stop, 50 Hz électriques/s maximum pour l'accélération, 1 à 10 000 ms pour
``DATA`` et 10 000 ms maximum pour le DS18B20. Une période DS18B20 inférieure à
750 ms est acceptée puis ramenée à 750 ms.

Contrôle moteur
---------------

``AppMotorControl_Start`` applique la configuration runtime, ajuste le PI vitesse
et démarre le moteur via MCSDK avec polarisation. La limite ``Iq`` est rampée en
RUN pour éviter une demande de couple brutale. ``AppMotorControl_Task`` surveille
les faults MCSDK, le dépassement de courant et la survitesse.

Le bouton B2 sur ``PC13`` dépose une requête dans l'interruption, puis la boucle
principale démarre à 2000 rpm avec 30 A maximum sur ``Iq`` et le courant total.
Toutes les 10 à 30 secondes, elle choisit un pas de 200 à 500 rpm et une
nouvelle cible dans la plage 2000–4000 rpm. La rampe MCSDK de 10 Hz
électriques/s lisse chaque transition ; avec deux paires de pôles, la pente
mécanique vaut 300 rpm/s. Un second appui arrête le moteur. Le traitement
différé et un anti-rebond de 250 ms évitent d'appeler le MCSDK depuis
l'interruption.

La consigne initiale est réappliquée lorsque MCSDK signale réellement ``RUN``.
Si un nouvel appui demande un démarrage pendant la phase d'arrêt asynchrone, la
requête attend ``IDLE`` et est retentée toutes les 100 ms. Les comparaisons de
deadline utilisent une soustraction signée et restent valides lors du
rebouclage du tick 32 bits.

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
entrées utilisateur pour guider l'opérateur. Le firmware garde les bornes
finales de 4500 rpm et 30 A, puis coupe le moteur en cas de fault MCSDK, courant
total trop élevé ou survitesse. Les sources Workbench, ``.ioc``, ``.wbdef`` et
les fichiers C générés utilisent les mêmes plafonds afin qu'une régénération ne
réintroduise pas les anciennes valeurs.

Le seuil de survitesse suit la consigne avec une marge, mais il est toujours
borné par le plafond absolu de 4500 rpm. Les appels directs à la configuration
normalisent également les valeurs ``NaN`` ou infinies vers les valeurs par
défaut avant de les transmettre à MCSDK.

Une télémétrie MCSDK de courant ou de vitesse non finie est traitée en mode
fail-safe : arrêt immédiat, désactivation du profil B2 et passage de la machine
d'états applicative en défaut. Elle ne peut donc pas contourner les comparaisons
de surintensité ou de survitesse.

La pleine échelle calculée du capteur de courant vaut environ 110 A avec un
shunt de 1 mΩ et un gain de 15. Cette marge de représentation ne constitue pas
une validation thermique de la carte. La consigne PolPulse reste à 14 A pour ne
pas transformer l'augmentation du plafond en impulsion de démarrage à 30 A. Le
seuil logiciel actif pendant PolPulse et le courant maximal du profileur DC
restent eux aussi bornés à 30 A.

Compilation et programmation
-----------------------------

Importer ``firmware_acquisition/tets_motor_dewalt/STM32CubeIDE`` comme projet
existant. Choisir ``Debug`` ou ``Release``, exécuter un clean build, puis
programmer la B-G473E-ZEST1S avec ST-LINK. Les sources sous ``Drivers``,
``MCSDK_v6.4.2-Full`` et une partie de ``Src``/``Inc`` sont générées ou tierces ;
les modifications fonctionnelles propres au dépôt doivent rester concentrées
dans ``STM32CubeIDE/Application/User`` et les interfaces applicatives associées.

Une régénération depuis STM32CubeMX ou Motor Control Workbench doit être revue
avant compilation : elle peut modifier les fichiers générés, les affectations
de broches et les constantes de courant.

Le chemin CMSIS/DSP du projet d'acquisition est relatif au dépôt dans
``.cproject`` ; le projet n'est plus dépendant d'un ancien dossier Workbench
utilisateur.

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

Le firmware de validation s'importe séparément depuis
``firmware_validation/STM32CubeIDE``. Son mode UART est choisi à la compilation ;
il faut donc effectuer un clean build et reflasher après toute modification de
``APP_NEAI_MODEL_ENABLED``.

Le contrôle moteur et le profil B2 aléatoire utilisent les mêmes limites et les
mêmes paramètres que le firmware d'acquisition. Le changement de vitesse
n'ajoute aucun texte sur l'UART afin de préserver le contrat NanoEdge.
