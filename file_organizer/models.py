"""Model adapter — reuses the existing OpenAIModel from downloads_organizer."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from downloads_organizer.models import OpenAIModel, RequestPacer

__all__ = ["OpenAIModel", "RequestPacer"]
