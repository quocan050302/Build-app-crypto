"""Cooperative cancellation of research jobs; never changes trading decisions."""
import json
import os
from pathlib import Path


def raise_if_research_aborted():
    marker = os.getenv("AURUM_RESOURCE_GUARD_ABORT_FILE")
    if not marker or not Path(marker).exists():
        return
    try:
        reason = json.loads(Path(marker).read_text(encoding="utf-8")).get("reason", "RESOURCE_GUARD_ABORT")
    except (OSError, ValueError, TypeError):
        reason = "UNREADABLE_RESOURCE_ABORT_MARKER"
    raise RuntimeError("VERIFICATION_ABORTED:" + str(reason))
