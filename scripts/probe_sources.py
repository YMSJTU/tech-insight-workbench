"""Read-only source connectivity check; does not create records or call a model."""

import json
from backend import sources

for name in ["crossref", "arxiv"]:
    try:
        rows = getattr(sources, name)("cache compression", 2)
        print(
            json.dumps(
                {
                    "source": name,
                    "ok": True,
                    "count": len(rows),
                    "first_title": rows[0]["title"] if rows else None,
                },
                ensure_ascii=True,
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "source": name,
                    "ok": False,
                    "error": type(exc).__name__ + ": " + str(exc)[:150],
                },
                ensure_ascii=True,
            )
        )
