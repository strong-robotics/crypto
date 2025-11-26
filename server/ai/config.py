"""
AI module configuration constants (overridable via env later if needed).
"""

import os
from typing import List

"""AI config bridged to server.config so one source of truth.
Falls back to reasonable defaults if server.config is unavailable.
"""
try:
    from config import config as server_config
    ENCODER_SEC: int = int(getattr(server_config, 'AUTO_BUY_ENTRY_SEC', 30))
    # Use ETA_BINS from server config for horizons if available; otherwise keep typical horizons
    HORIZONS: List[int] = list(getattr(server_config, 'ETA_BINS', [30, 60, 180]))
    TARGET_RETURN: float = float(getattr(server_config, 'TARGET_RETURN', 0.15))
except Exception:
    # Fallback defaults
    ENCODER_SEC: int = 30
    HORIZONS: List[int] = [30, 60, 180]
    TARGET_RETURN: float = 0.15

# Directory with models (store artifacts under server/ai/models)
# Use absolute path relative to this file to avoid CWD issues.
_BASE_DIR = os.path.dirname(__file__)
MODELS_DIR: str = os.path.join(_BASE_DIR, "models")

# Verbose debug logging for forecast loop
FORECAST_DEBUG: bool = False
