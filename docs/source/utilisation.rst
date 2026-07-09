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

   #CSV_HEADER,stm32_time_ms,d6t_temp_c,ds18b20_temp_c,motor_ud_v,motor_uq_v,motor_speed_mech_rpm,motor_id_a,motor_iq_a
   DATA,1000,31.2,24.5,1.125,3.480,600.0,-0.120,1.200

Si le module IR n'est pas branché ou ne répond pas, la ligne reste valide :

.. code-block:: text

   DATA,1000,NaN,24.5,1.125,3.480,600.0,-0.120,1.200

Colonnes CSV finales
--------------------

Le fichier CSV écrit par le dashboard conserve les colonnes suivantes, dans cet
ordre :

.. code-block:: text

   stm32_time_ms;d6t_temp_c;ds18b20_temp_c;motor_ud_v;motor_uq_v;motor_speed_mech_rpm;motor_id_a;motor_iq_a