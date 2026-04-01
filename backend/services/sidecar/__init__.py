"""
Sidecar Modules Package
=======================
Decoupled modules that enhance the core engine without modifying it.
"""

from .hook_bait_detector import HookBaitDetector, get_hook_bait_detector, ENABLE_HOOK_BAIT_DETECTOR

__all__ = [
    "HookBaitDetector",
    "get_hook_bait_detector", 
    "ENABLE_HOOK_BAIT_DETECTOR"
]
