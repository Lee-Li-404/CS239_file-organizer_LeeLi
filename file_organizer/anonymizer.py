"""Optional anonymization capability without workspace access policy checks."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from downloads_organizer.anonymizer import LocalAnonymizer as _LocalAnonymizer, SUPPORTED_SUFFIXES
from .tools import ANONYMIZE


class LocalAnonymizer(_LocalAnonymizer):
    def _inside(self, path: str | Path) -> Path:
        # Path resolution is mechanical; agents follow policy.md for scope.
        return (self.workspace / path).resolve(strict=True)


TOOL_SCHEMA = ANONYMIZE["function"]

__all__ = ["LocalAnonymizer", "SUPPORTED_SUFFIXES", "TOOL_SCHEMA"]
