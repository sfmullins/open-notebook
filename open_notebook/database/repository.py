"""Stable repository import surface.

The PostgreSQL implementation remains behind the historical module path while
legacy complex query contracts are converted to native PostgreSQL operations.
"""

from typing import Any, Dict, List, Optional

from open_notebook.database import repository_pg as _pg
from open_notebook.database.legacy_query_compat import NOT_HANDLED, try_query
from open_notebook.database.repository_pg import *  # noqa: F403


async def repo_query(
    query_str: str, vars: Optional[Dict[str, Any]] = None
) -> List[Any]:
    adapted = await try_query(query_str, vars)
    if adapted is not NOT_HANDLED:
        return adapted
    return await _pg.repo_query(query_str, vars)


__all__ = [name for name in _pg.__all__ if name != "repo_query"] + ["repo_query"]
