"""One-off migration: add `state_signature` to existing footprint cards (idempotent).

    python tools/learning/add_state_signature.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hibot.agent import cards  # noqa: E402

TICKETS = ROOT / "knowledge" / "incidents" / "tickets_enriched.json"


def main() -> int:
    tickets = {}
    if TICKETS.exists():
        data = json.loads(TICKETS.read_text(encoding="utf-8"))
        tickets = {t["key"]: (t.get("text_clean") or {}) for t in data.get("tickets", [])}
    done = cards.migrate_state_signatures(tickets=tickets)
    print("state_signature added to %d card(s): %s" % (len(done), ", ".join(done) or "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
