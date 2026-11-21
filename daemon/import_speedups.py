"""Import-time trims for third-party packages the daemon never uses.

``import insightface.app`` drags in ``insightface.app.mask_renderer``, which
imports the whole ``face3d`` mesh toolkit: matplotlib, albumentations and
scikit-image. The daemon only uses detection and recognition, so those ~3.5 s
(measured; far more on a cold disk cache) are pure start-up latency. Registering
an empty stand-in for the module *before* InsightFace is imported lets the real
package load without them. Nothing else in InsightFace references the mask
renderer, and a missing/renamed module is simply ignored.
"""
from __future__ import annotations

import sys
import types

_UNUSED_INSIGHTFACE_MODULES = ("insightface.app.mask_renderer",)


def skip_unused_insightface_modules() -> list[str]:
    """Stub heavy, unused InsightFace submodules. Returns the names stubbed."""
    stubbed: list[str] = []
    for name in _UNUSED_INSIGHTFACE_MODULES:
        if name in sys.modules:
            continue
        stand_in = types.ModuleType(name)
        stand_in.__all__ = []  # type: ignore[attr-defined]
        stand_in.__doc__ = "Stubbed by MajestyGuard import_speedups (unused, slow to import)."
        sys.modules[name] = stand_in
        stubbed.append(name)
    return stubbed
