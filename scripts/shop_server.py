"""Launcher for texas-grocery-mcp that applies our GraphQL hash overrides at startup
(config/graphql-hashes.json, harvested by refresh_graphql_hashes.py). The installed
package is never modified — overrides live in memory for this process only."""

import json
from pathlib import Path

HASHES_JSON = Path(__file__).resolve().parents[1] / "config" / "graphql-hashes.json"


def main() -> None:
    if HASHES_JSON.exists():
        from texas_grocery_mcp.clients import graphql
        overrides = json.loads(HASHES_JSON.read_text())
        applied = {op: sha for op, sha in overrides.items() if op in graphql.PERSISTED_QUERIES}
        graphql.PERSISTED_QUERIES.update(applied)
        # New operations the package doesn't know yet are harmless to include too.
        graphql.PERSISTED_QUERIES.update(overrides)

    from texas_grocery_mcp.server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
