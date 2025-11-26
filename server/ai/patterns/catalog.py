"""Pattern catalog and codes (v1).

Used by seeds and potential model classifiers.
"""

try:  # Python 3.11+
    from enum import StrEnum  # type: ignore
except ImportError:  # Python 3.9/3.10 fallback
    from enum import Enum

    class StrEnum(str, Enum):  # minimal shim
        pass
from typing import List, Dict


class PatternCode(StrEnum):
    UNKNOWN = "unknown"
    
    BEST_ONE = "best_one"
    RISING_PHOENIX = "rising_phoenix"
    WAVE_RIDER = "wave_rider"
    CLEAN_LAUNCH = "clean_launch"
    CALM_STORM = "calm_storm"
    GRAVITY_BREAKER = "gravity_breaker"
    GOLDEN_CURVE = "golden_curve"
    BAIT_SWITCH = "bait_switch"
    ECHO_WAVE = "echo_wave"
    FLASH_BLOOM = "flash_bloom"
    TUG_OF_WAR = "tug_of_war"
    DRUNKEN_SAILOR = "drunken_sailor"
    ICE_MELT = "ice_melt"
    RUG_PREQUEL = "rug_prequel"
    DEATH_SPIKE = "death_spike"
    FLATLINER = "flatliner"
    SMOKE_BOMB = "smoke_bomb"
    MIRAGE_RISE = "mirage_rise"
    PANIC_SINK = "panic_sink"
    BLACK_HOLE = "black_hole"


PATTERN_SEED: List[Dict] = [
    {"code": PatternCode.UNKNOWN,           "name": "UNKNOWN",          "tier": "top",    "score": 101},

    {"code": PatternCode.BEST_ONE,          "name": "The Best One",     "tier": "top",    "score": 100},
    {"code": PatternCode.RISING_PHOENIX,    "name": "Rising Phoenix",   "tier": "top",    "score": 95},
    {"code": PatternCode.WAVE_RIDER,        "name": "Wave Rider",       "tier": "top",    "score": 92},
    {"code": PatternCode.CLEAN_LAUNCH,      "name": "Clean Launch",     "tier": "top",    "score": 90},
    {"code": PatternCode.CALM_STORM,        "name": "Calm Storm",       "tier": "top",    "score": 88},
    {"code": PatternCode.GRAVITY_BREAKER,   "name": "Gravity Breaker",  "tier": "top",    "score": 86},
    {"code": PatternCode.GOLDEN_CURVE,      "name": "Golden Curve",     "tier": "top",    "score": 85},
    {"code": PatternCode.BAIT_SWITCH,       "name": "Bait & Switch",    "tier": "middle", "score": 60},
    {"code": PatternCode.ECHO_WAVE,         "name": "Echo Wave",        "tier": "middle", "score": 58},
    {"code": PatternCode.FLASH_BLOOM,       "name": "Flash Bloom",      "tier": "middle", "score": 55},
    {"code": PatternCode.TUG_OF_WAR,        "name": "Tug of War",       "tier": "middle", "score": 52},
    {"code": PatternCode.DRUNKEN_SAILOR,    "name": "Drunken Sailor",   "tier": "middle", "score": 50},
    {"code": PatternCode.ICE_MELT,          "name": "Ice Melt",         "tier": "middle", "score": 48},
    {"code": PatternCode.RUG_PREQUEL,       "name": "Rug Prequel",      "tier": "bottom", "score": 20},
    {"code": PatternCode.DEATH_SPIKE,       "name": "Death Spike",      "tier": "bottom", "score": 15},
    {"code": PatternCode.FLATLINER,         "name": "Flatliner",        "tier": "bottom", "score": 10},
    {"code": PatternCode.SMOKE_BOMB,        "name": "Smoke Bomb",       "tier": "bottom", "score": 10},
    {"code": PatternCode.MIRAGE_RISE,       "name": "Mirage Rise",      "tier": "bottom", "score": 8},
    {"code": PatternCode.PANIC_SINK,        "name": "Panic Sink",       "tier": "bottom", "score": 5},
    {"code": PatternCode.BLACK_HOLE,        "name": "Black Hole",       "tier": "bottom", "score": 1},
]
