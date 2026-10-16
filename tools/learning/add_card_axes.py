"""One-off migration (round 1): add `root_cause_type` / `resolution_action` to existing cards (idempotent).

    python tools/learning/add_card_axes.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hibot.agent import cards  # noqa: E402


def main() -> int:
    done = cards.migrate_axes()
    print("axes added to %d card(s): %s" % (len(done), ", ".join(done) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
