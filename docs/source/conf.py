# Configuration file for the Sphinx documentation builder.
import os
import sys
from pathlib import Path

# Permet à Sphinx de trouver le fichier Python situé à la racine du projet.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

project = "STM32 PMSM Datalog Dashboard"
copyright = '2026, Melvin Pellegrino'
author = "Melvin Pellegrino"
release = "1.0"
language = "fr"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosummary",
]

autosummary_generate = True

templates_path = ["_templates"]
exclude_patterns = []

html_theme = "sphinx_rtd_theme"

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "special-members": "__init__",
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_param = True
napoleon_use_rtype = True