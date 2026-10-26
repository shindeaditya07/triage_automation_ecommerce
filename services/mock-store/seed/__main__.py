"""`make seed`: build the seed, check it against the data contract and write seed.json."""

import json
import sys
from pathlib import Path

from ..clock import current_time
from . import build_seed

OUTPUT = Path(__file__).with_name("seed.json")


def main() -> int:
    data = build_seed(current_time())
    document = json.dumps(data.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(document, encoding="utf-8")
    print(
        f"seed as of {data.now.isoformat()}: {len(data.scenarios)} scenarios, "
        f"{len(data.orders)} orders, {len(data.histories)} customers -> {OUTPUT.name}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
