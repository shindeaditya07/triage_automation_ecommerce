"""Write the API's OpenAPI schema as JSON, for generating the frontend's types.

    uv run python -m scripts.dump_openapi apps/web/openapi.json

Builds the app without starting it, so it needs no database or secrets.
"""

import json
import sys

from services.api.main import create_app


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: python -m scripts.dump_openapi OUTPUT.json\n")
        return 2
    schema = create_app().openapi()
    with open(argv[1], "w", encoding="utf-8") as out:
        json.dump(schema, out, indent=2, sort_keys=True)
        out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
