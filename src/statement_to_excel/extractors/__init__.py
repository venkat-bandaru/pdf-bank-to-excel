"""Extractors sub-package — one module per supported bank layout.

Each module exposes a class implementing the Extractor protocol from base.py.
Adding support for a new bank means adding a new file here; existing files
must not be modified to handle a different bank.
"""

from __future__ import annotations
