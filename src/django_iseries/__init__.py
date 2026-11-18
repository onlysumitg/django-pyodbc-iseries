"""Db2 for iSeries support for Django framework."""

__version__ = "2.0.0"

import re

from django.core.exceptions import ImproperlyConfigured

try:
    import pyodbc as Database
except ImportError as e:
    raise ImproperlyConfigured(f'Error loading pyodbc module: {e}') from e

pyodbc_version = tuple(int(part) for part in re.findall(r'\d+', Database.version)[:3])
if pyodbc_version < (4, 0, 39):
    raise ImproperlyConfigured(f'pyodbc 4.0.39 or later is required; you have {Database.version}.')
