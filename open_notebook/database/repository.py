"""Stable repository import surface.

The implementation lives in :mod:`open_notebook.database.repository_pg` while
call sites are migrated away from legacy query strings.
"""

from open_notebook.database.repository_pg import *  # noqa: F403
from open_notebook.database.repository_pg import __all__
