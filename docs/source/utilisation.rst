Utilisation
===========

Connexion
---------

Sélectionner le port COM associé à la carte STM32, puis vérifier le baudrate.
La valeur par défaut est 115200 bauds.

Profil moteur
-------------

Le profil moteur définit les paramètres envoyés à la carte :

* vitesse cible en rpm ;
* vitesse électrique équivalente ;
* nombre de paires de pôles ;
* limite Iq ;
* limite hard stop ;
* rampe d'accélération.

Acquisition
-----------

La période DATA définit la fréquence d'enregistrement des données moteur.
La période DS18B20 définit la fréquence de mesure du capteur de température externe.

Le capteur DS18B20 ne doit pas être configuré sous 750 ms pour obtenir une
nouvelle mesure fiable.

Protocole série
---------------

Le logiciel envoie les commandes suivantes à la carte :

.. code-block:: text

   SYNC
   CFG,<target_rpm>,<iq_limit>,<hard_limit>,<accel>,<datalog_ms>,<ds18b20_ms>
   START
   STOP

La carte doit répondre avec :

.. code-block:: text

   ACK,SYNC
   ACK,CFG
   ACK,START
   ACK,STOP

Les données doivent être envoyées sous la forme :

.. code-block:: text

   #CSV_HEADER,stm32_time_ms,ds18b20_temp_c,motor_vbus_v,motor_iq_a
   DATA,1000,24.5,24.0,1.2