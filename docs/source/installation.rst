Installation
============

Prérequis
---------

Le dashboard nécessite Python 3, ainsi que les bibliothèques suivantes :

* pyserial
* matplotlib
* tkinter

Installation des dépendances
----------------------------

Depuis la racine du projet :

.. code-block:: powershell

   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt

Lancement du programme
----------------------

.. code-block:: powershell

   python motor_datalog_gui_dashboard.py