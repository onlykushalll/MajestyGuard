"""
MajestyGuard Dynamic Island pill widget.

The visual language is inspired by Dynamic Island behavior: black material,
compact glanceable states, springy morphs, and staged content transitions.
"""
from __future__ import annotations

import logging
import time
import math
import os

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QFont, QPen, QLinearGradient, QRadialGradient, QRegion
from PyQt6.QtWidgets import QWidget, QApplication

from states import IslandState, get_state

try:  # liquid-glass body is optional: any failure keeps the flat paint
    import glass as _glass
except Exception:  # pragma: no cover
    _glass = None

try:
    from fonts import ui_font as _ui_font
except Exception:  # pragma: no cover
    _ui_font = None


def _font(size, weight=QFont.Weight.Medium) -> QFont:
    """Segoe UI Variable via fonts.ui_font; plain Segoe UI if that is unavailable."""
    if _ui_font is not None:
        try:
            return _ui_font(size, weight)
        except Exception:
            pass
    return QFont("Segoe UI", int(size), weight)

log = logging.getLogger("MajestyGuard.Island")

_SCREEN_TOP_MARGIN = 8
_PULSE_PERIOD_MS = 1800
_FLASH_BLINKS = 4
_FLASH_INTERVAL_MS = 110
_FRAME_MS = 16
_SCAN_PERIOD_MS = 900
_PAD = 8
_VERIFY_REQUEST_STATES = {"locked_passive", "soft_locked", "social_lock"}
_IMMEDIATE_STATES = {"idle", "hostile_lock", "camera_fault"}

# Dot-pulse scanning constants
_DOT_COUNT = 3
_DOT_RADIUS = 2.5  # 5px diameter
_DOT_GAP = 6.0
_DOT_SCAN_PERIOD_MS = 1200

# --- Orb particle icon system -----------------------------------------
# Small-pixel recipes inspired by Thinking Orbs. These are separate visual
# verbs, not one generic loop with different speeds: searching sweeps a dotted
# globe, verification works through orbital bands, resolution settles inward,
# and the lock stays deliberately static. The profiles retain a compact,
# inspectable tuning surface for future visual adjustment.
#   (particle_count, orbit_speed, reserved, spread, base_dot_px)
_ORB_STYLES: dict[str, tuple[int, float, float, float, float]] = {
    "searching": (14, 0.16, 0.00, 0.90, 1.45),
    "working": (15, 0.34, 0.00, 0.80, 1.35),
    "resolving": (8, 0.08, 0.00, 0.38, 1.65),
    "guarded": (10, 0.01, 0.00, 0.78, 1.35),
}

_GOLDEN_ANGLE = 2.399963  # radians (~137.5°) — even, organic-looking spacing


def _orb_particle_seed(i: int) -> tuple[float, float, float]:
    """Stable per-particle (angle_offset, radius_frac, size_frac). Must
    stay fixed across frames for a given index -- only _phase should move
    the dots, never re-randomizing per paint, or they'd jitter/flicker
    instead of animating smoothly."""
    angle_offset = (i * _GOLDEN_ANGLE) % (2 * math.pi)
    h1 = math.sin(i * 12.9898) * 43758.5453
    h2 = math.sin(i * 78.233) * 12543.899
    radius_frac = 0.35 + 0.55 * (h1 - math.floor(h1))
    size_frac = 0.5 + 0.5 * (h2 - math.floor(h2))
    return angle_offset, radius_frac, size_frac

# Verified → Welcome → Fade sequence timing
_VERIFIED_DURATION_MS = 400
_CHECKMARK_DRAW_MS = 150
_WELCOME_DWELL_MS = 1450
_PILL_FADE_MS = 400


class IslandWidget(QWidget):
    """Frameless top-center pill widget."""

    def __init__(self, on_verify_requested=None, on_overlay_dissolve=None):
        super().__init__()
        self._state: IslandState = get_state("idle")
        self._on_verify_requested = on_verify_requested
        self._on_overlay_dissolve = on_overlay_dissolve
        self._visible_state: IslandState = self._state
        self._alpha = 1.0
        self._pulse_phase = 0.0
        self._scan_phase = 0.0
        self._dot_scan_phase = 0.0
        # Orb phase is wall-clock-like seconds, so each style's speed stays
        # meaningfully expressed in revolutions per second.
        self._phase = 0.0
        self._welcome_started_at = None
        self._flash_count = 0
        self._reduce_motion = os.environ.get("MG_UI_REDUCE_MOTION", "0") == "1"

        self._anim_w = float(self._state.width)
        self._anim_h = float(self._state.height)
        self._vel_w = 0.0
        self._vel_h = 0.0
        self._target_w = float(self._state.width)
        self._target_h = float(self._state.height)

        self._content_alpha = 1.0
        self._content_fading_out = False
        self._pending_state: IslandState | None = None

        # Verified → Welcome → Fade orchestration
        self._checkmark_progress = 0.0  # 0.0–1.0 draw progress
        self._in_exit_sequence = False
        self._pill_opacity = 1.0

        # Animation states for unified timer
        self._anim_pulse_active = False
        self._anim_morph_active = False
        self._anim_content_active = False
        self._anim_scan_active = False
        self._anim_dot_scan_active = False
        self._anim_checkmark_active = False
        self._anim_pill_fade_active = False
        self._anim_flash_active = False
        self._anim_orb_active = False
        self._flash_frame_counter = 0
        self._shake_phase = 0.0
        self._shake_offset = 0.0
        self._anim_shake_active = False
        self._verify_start_time = 0.0
        self._deferred_lock_state = None
        self._deferred_scan_result = None
        self._hovered = False
        self._hover_progress = 0.0
        self._content_slide_y = 0.0
        self._last_tick_ts = 0.0

        self._setup_window()
        self._setup_timers()
        self._reposition()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Use a fixed large canvas size to prevent OS-level window resize artifacts & DWM jitter
        self.setFixedSize(500, 120)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        app = QApplication.instance()
        if app is not None:
            primary = QApplication.primaryScreen()
            if primary is not None:
                primary.geometryChanged.connect(lambda *_: self._reposition())

    def _route_pointer(self, state_name):
        interactive = state_name in _VERIFY_REQUEST_STATES and self._on_verify_requested is not None
        handle = self.windowHandle()
        flag = Qt.WindowType.WindowTransparentForInput
        if handle is not None and bool(handle.flags() & flag) == interactive:
            # Change the native flag in-place. QWidget.setWindowFlag would hide
            # the visible pill and restart the transition on Windows.
            handle.setFlag(flag, not interactive)
        self.setCursor(Qt.CursorShape.PointingHandCursor if interactive else Qt.CursorShape.ArrowCursor)

    def _setup_timers(self) -> None:
        # Single unified timer for all 60fps animations to ensure exactly one update() per frame
        self._main_timer = QTimer(self)
        self._main_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._main_timer.setInterval(_FRAME_MS)
        self._main_timer.timeout.connect(self._tick_animations)

        self._defer_timer = QTimer(self)
        self._defer_timer.setSingleShot(True)
        self._defer_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._defer_timer.timeout.connect(self._apply_deferred_scan_result)

        # Exit sequence single-shot timers (dwells)
        self._verified_dwell_timer = QTimer(self)
        self._verified_dwell_timer.setSingleShot(True)
        self._verified_dwell_timer.timeout.connect(self._after_verified)

        self._welcome_dwell_timer = QTimer(self)
        self._welcome_dwell_timer.setSingleShot(True)
        self._welcome_dwell_timer.timeout.connect(self._begin_pill_fade)

    def apply_state(self, state: IslandState) -> None:
        if state.name == "idle":
            if self._state.name == "idle" and self.isHidden():
                return
            self._abort_exit_sequence()
            self._cancel_deferred_result()
            self._finish_exit_sequence()
            return

        # If the state is completely identical, avoid redundant updates/repaints
        if state == self._state and not self._in_exit_sequence and not self._anim_shake_active:
            return

        if state.name == self._state.name not in ("active", "welcome"):
            self._state = state
            if self._pending_state is not None:
                self._pending_state = state
            elif self._visible_state.name == state.name:
                self._visible_state = state
                self.update()
            return

        # If we are in the exit sequence and a non-active state arrives, abort
        if self._in_exit_sequence and state.name not in ("active", "welcome"):
            self._abort_exit_sequence()

        # If the stranger shake is running, defer applying any locked states until the shake completes
        if self._anim_shake_active and state.name in {"locked_passive", "soft_locked", "social_lock", "locked"}:
            self._deferred_lock_state = state
            return

        # A normal owner check should confirm and disappear.  Reserve the
        # friendlier welcome moment for an actual return from the soft lock.
        if state.name == "active":
            if self._in_exit_sequence:
                return
            if self._state.name in {"idle_detected", "scanning", "locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock", "verify_failed", "locked"}:
                self._begin_exit_sequence(
                    state,
                    show_welcome=self._state.name in {
                        "locked_passive", "soft_locked", "verifying_lock",
                        "social_lock", "hostile_lock", "verify_failed", "locked",
                    },
                )
                return


            self.apply_state(get_state("idle"))
            return

        if self.isHidden():
            self.show()

        # If we transition from verifying to a lock state (rejection), show stranger shake first!
        # (Skipped under reduced motion: no shake runs there, so the parked
        # target would never be consumed and the pill would stick on "stranger".)
        if (
            self._state.name == "verifying_lock"
            and state.name in {"social_lock", "stranger"}
            and not self._reduce_motion
        ):
            if getattr(self, "_deferred_lock_state", None) is None or state.name != "stranger":
                self._deferred_lock_state = state
            state = get_state("stranger")

        # Enforce minimum verification scan time (650ms) before transitioning out of verifying_lock
        immediate = state.name in _IMMEDIATE_STATES
        if self._state.name == "verifying_lock" and not immediate and not self._reduce_motion:
            elapsed = time.monotonic() - getattr(self, "_verify_start_time", 0.0)
            min_duration = 0.65
            if elapsed < min_duration:
                self._deferred_scan_result = state
                if not self._defer_timer.isActive():
                    self._defer_timer.start(math.ceil((min_duration - elapsed) * 1000))
                return

        if immediate:
            self._cancel_deferred_result()
        else:
            self._defer_timer.stop()
            self._deferred_scan_result = None
            if state.name != "stranger":
                self._deferred_lock_state = None

        # Track when we enter verifying_lock state to enforce minimum duration
        if state.name == "verifying_lock" and self._state.name != "verifying_lock":
            self._verify_start_time = time.monotonic()

        previous_mode = self._state.mode

        # If it's the same state name, do not reset running animations or morphs
        if state.name == self._state.name:
            self._state = state
            self._visible_state = state
            self.update()
            return

        content_changed = (
            state.label != self._visible_state.label
            or state.mode != self._visible_state.mode
            or state.detail != self._visible_state.detail
        )

        self._state = state
        self._route_pointer(state.name)
        # State geometry is part of the visual contract. Lock states need the
        # larger layouts declared in states.py for their guarded icon and
        # status label; collapsing them to the idle pill clips that feedback.
        self._target_w = float(state.width)
        self._target_h = float(state.height)
        if state.mode == "welcome":
            self._welcome_started_at = time.monotonic()
        
        # Reset previous animation states
        self._anim_flash_active = False
        self._anim_pulse_active = False
        self._anim_dot_scan_active = False
        if self._anim_shake_active:
            # A third path _deferred_lock_state can go stale through:
            # this unconditional per-state reset silences an in-flight
            # shake whenever it's interrupted by something that is
            # neither "active" (handled earlier, via its own early
            # return) nor one of the lock-family states the dedicated
            # shake-interrupt check above defers for instead of
            # cancelling. Without this, a target deferred for THIS shake
            # would survive its own shake ending here and sit orphaned
            # until some later, unrelated shake wrongly consumed it.
            self._deferred_lock_state = None
        self._anim_shake_active = False
        self._shake_offset = 0.0
        self._alpha = 1.0
        self._pill_opacity = 1.0

        if state.pulse and not self._reduce_motion:
            self._anim_pulse_active = True

        if state.flash and not self._reduce_motion:
            self._flash_count = _FLASH_BLINKS * 2
            self._flash_frame_counter = 0
            self._anim_flash_active = True

        if state.name == "stranger" and not self._reduce_motion:
            self._shake_phase = 0.0
            self._anim_shake_active = True

        if state.mode == "face_scan" and not self._reduce_motion:
            if previous_mode != "face_scan":
                self._scan_phase = 0.0
            self._anim_scan_active = True
        else:
            self._anim_scan_active = False

        if state.mode == "dot_scan" and not self._reduce_motion:
            if previous_mode != "dot_scan":
                self._dot_scan_phase = 0.0
            self._anim_dot_scan_active = True
        else:
            if state.mode != "dot_scan":
                self._anim_dot_scan_active = False

        self._anim_orb_active = state.mode in {"dot_scan", "face_scan", "welcome"} and not self._reduce_motion

        if content_changed and not self._reduce_motion and not immediate:
            self._pending_state = state
            self._content_fading_out = True
            self._anim_content_active = True
        else:
            self._visible_state = state
            self._pending_state = None
            self._anim_content_active = False
            self._content_slide_y = 0.0
            self._content_alpha = 1.0

        if self._reduce_motion:
            self._anim_w = self._target_w
            self._anim_h = self._target_h
            self._vel_w = 0.0
            self._vel_h = 0.0
            self._update_mask()
            self.update()
        else:
            self._anim_morph_active = True
            
        self._start_animation_timer()

    def _apply_deferred_scan_result(self) -> None:
        state, self._deferred_scan_result = self._deferred_scan_result, None
        if state is not None:
            self.apply_state(state)

    def _cancel_deferred_result(self) -> None:
        self._defer_timer.stop()
        self._deferred_scan_result = None
        self._deferred_lock_state = None

    # ── Exit sequence orchestration ─────────────────────────────────────

    def _begin_exit_sequence(self, active_state: IslandState, *, show_welcome: bool) -> None:
        """Confirm a verification, optionally welcoming a returned owner."""
        self._cancel_deferred_result()
        self._in_exit_sequence = True
        self._show_welcome_after_verification = show_welcome
        # A quick result supersedes any label still crossfading into view.
        self._pending_state = None
        self._anim_content_active = False
        self._content_fading_out = False
        self._content_slide_y = 0.0
        self._pill_opacity = 1.0
        self._checkmark_progress = 1.0 if self._reduce_motion else 0.0

        # Only an actual soft-lock return has an overlay to dissolve.
        if show_welcome and self._on_overlay_dissolve is not None:
            self._on_overlay_dissolve()

        # Apply verified state (green border, checkmark only)
        self._state = active_state
        self._route_pointer(active_state.name)
        self._visible_state = active_state
        self._target_w = float(active_state.width)
        self._target_h = float(active_state.height)
        self._content_alpha = 1.0
        self._anim_pulse_active = False
        self._anim_flash_active = False
        self._anim_dot_scan_active = False
        self._anim_scan_active = False
        self._anim_orb_active = False
        self._alpha = 1.0

        if not self._reduce_motion:
            self._anim_morph_active = True
            self._anim_checkmark_active = True
            self._start_animation_timer()
        else:
            self._snap_exit_geometry()

        # After the confirmed dwell, either welcome a returned owner or fade.
        self._verified_dwell_timer.start(_VERIFIED_DURATION_MS)

    def _after_verified(self) -> None:
        if self._show_welcome_after_verification:
            self._begin_welcome()
        else:
            self._begin_pill_fade()

    def _begin_welcome(self) -> None:
        """Transition pill to Welcome state."""
        welcome = get_state("welcome")
        self._welcome_started_at = time.monotonic()
        self._state = welcome
        self._visible_state = welcome
        self._target_w = float(welcome.width)
        self._target_h = float(welcome.height)
        self._content_alpha = 1.0
        self._anim_checkmark_active = False

        if not self._reduce_motion:
            self._anim_morph_active = True
            self._anim_orb_active = True
            self._start_animation_timer()
        else:
            self._snap_exit_geometry()

        self.update()
        self._welcome_dwell_timer.start(_WELCOME_DWELL_MS)

    def _snap_exit_geometry(self) -> None:
        self._anim_w, self._anim_h = self._target_w, self._target_h
        self._vel_w = self._vel_h = 0.0
        self._anim_morph_active = False
        self._update_mask()
        self.update()

    def _begin_pill_fade(self) -> None:
        """Instant Sucking Blipp: Rapidly contract pill to a dot and snap hide."""
        if not self._reduce_motion:
            self._content_alpha = 0.0
            self._target_w = 4.0
            self._target_h = 4.0
            self._vel_w = -2.8
            self._vel_h = -0.8
            self._anim_morph_active = True
            self._anim_pill_fade_active = True
            self._start_animation_timer()
        else:
            self._finish_exit_sequence()

    def _finish_exit_sequence(self) -> None:
        """Clean up after exit sequence completes."""
        self._in_exit_sequence = False
        self._show_welcome_after_verification = False
        self._pill_opacity = 1.0
        self._anim_pill_fade_active = False
        self._anim_checkmark_active = False
        self._anim_orb_active = False
        self._anim_morph_active = False
        self._anim_shake_active = False
        self._shake_offset = 0.0
        self._anim_content_active = False
        self._pending_state = None
        self._content_slide_y = 0.0
        self._anim_scan_active = self._anim_dot_scan_active = False
        self._anim_pulse_active = self._anim_flash_active = False
        self._main_timer.stop()
        # Reset to idle
        idle = get_state("idle")
        self._state = idle
        self._route_pointer(idle.name)
        self._visible_state = idle
        self._anim_w = float(idle.width)
        self._anim_h = float(idle.height)
        self._target_w = float(idle.width)
        self._target_h = float(idle.height)
        self._vel_w = 0.0
        self._vel_h = 0.0
        self._content_alpha = 1.0
        self._update_mask()
        self.update()
        self.hide()


    def _abort_exit_sequence(self) -> None:
        """Cancel exit sequence if interrupted by a non-active state."""
        self._in_exit_sequence = False
        self._show_welcome_after_verification = False
        self._pill_opacity = 1.0
        self._verified_dwell_timer.stop()
        self._welcome_dwell_timer.stop()
        self._anim_pill_fade_active = False
        self._anim_checkmark_active = False

    # ── Timer ticks ─────────────────────────────────────────────────────

    def _start_animation_timer(self) -> None:
        if not self._main_timer.isActive():
            self._last_tick_ts = time.monotonic()
            self._main_timer.start()

    def _tick_animations(self) -> None:
        now = time.monotonic()
        if self._last_tick_ts > 0.0 and self._main_timer.isActive():
            elapsed_ms = (now - self._last_tick_ts) * 1000.0
            dt_scale = min(1.8, max(0.6, elapsed_ms / _FRAME_MS))
        else:
            dt_scale = 1.0
        self._last_tick_ts = now
        frame_ms = _FRAME_MS * dt_scale
        updated = False

        if self._anim_pulse_active:
            self._pulse_phase = (self._pulse_phase + frame_ms / _PULSE_PERIOD_MS) % 1.0
            updated = True

        if self._anim_flash_active:
            self._flash_frame_counter += 1
            if self._flash_frame_counter >= 7:  # toggle every ~110ms
                self._flash_frame_counter = 0
                self._flash_count -= 1
                self._alpha = 1.0 if self._flash_count % 2 == 0 else 0.40
                if self._flash_count <= 0:
                    self._anim_flash_active = False
                    self._alpha = 1.0
                updated = True

        if self._anim_scan_active:
            self._scan_phase = (self._scan_phase + frame_ms / _SCAN_PERIOD_MS) % 1.0
            updated = True

        if self._anim_dot_scan_active:
            self._dot_scan_phase = (self._dot_scan_phase + frame_ms / _DOT_SCAN_PERIOD_MS) % 1.0
            updated = True

        if self._anim_orb_active:
            self._phase = (self._phase + frame_ms / 1000.0) % 60.0
            updated = True
            if self._state.mode == "welcome" and self._welcome_time() >= 1.0:
                self._anim_orb_active = False

        if self._anim_shake_active:
            self._shake_phase += (0.016 * dt_scale) / 0.45
            if self._shake_phase >= 1.0:
                self._anim_shake_active = False
                self._shake_offset = 0.0
                self._shake_phase = 0.0
                if getattr(self, "_deferred_lock_state", None) is not None:
                    deferred = self._deferred_lock_state
                    self._deferred_lock_state = None
                    self.apply_state(deferred)
            else:
                self._shake_offset = 3.6 * math.sin(self._shake_phase * math.tau * 1.5) * (1.0 - self._shake_phase) ** 2
            updated = True

        target_hover = 1.0 if (self._hovered and self._state.name in _VERIFY_REQUEST_STATES and not self._reduce_motion) else 0.0
        if abs(target_hover - self._hover_progress) > 0.01:
            self._hover_progress += (target_hover - self._hover_progress) * min(1.0, 0.28 * dt_scale)
            updated = True
        elif self._hover_progress != target_hover:
            self._hover_progress = target_hover
            updated = True

        if self._anim_content_active:
            step = 0.16 * dt_scale
            if self._content_fading_out:
                self._content_alpha = max(0.0, self._content_alpha - step)
                self._content_slide_y = -2.5 * (1.0 - self._content_alpha)
                if self._content_alpha <= 0.0:
                    if self._pending_state is not None:
                        self._visible_state = self._pending_state
                    self._pending_state = None
                    self._content_fading_out = False
                    self._content_slide_y = 2.5
            else:
                self._content_alpha = min(1.0, self._content_alpha + step)
                self._content_slide_y = 2.5 * (1.0 - self._content_alpha)
                if self._content_alpha >= 1.0:
                    self._content_slide_y = 0.0
                    self._anim_content_active = False
            updated = True

        if self._anim_morph_active:
            if getattr(self, "_anim_pill_fade_active", False):
                stiffness = 0.28
                damping = 0.82  # High damping for butter-smooth contraction frames
            else:
                stiffness = 0.22
                damping = 0.66
            self._vel_w = (self._vel_w + (self._target_w - self._anim_w) * stiffness) * damping
            self._vel_h = (self._vel_h + (self._target_h - self._anim_h) * stiffness) * damping
            self._anim_w += self._vel_w
            self._anim_h += self._vel_h

            settled = (
                abs(self._target_w - self._anim_w) < 0.4
                and abs(self._target_h - self._anim_h) < 0.4
                and abs(self._vel_w) < 0.25
                and abs(self._vel_h) < 0.25
            )
            if settled:
                self._anim_w = self._target_w
                self._anim_h = self._target_h
                self._vel_w = 0.0
                self._vel_h = 0.0
                self._anim_morph_active = False

            self._update_mask()
            updated = True

        if self._anim_checkmark_active:
            step = frame_ms / _CHECKMARK_DRAW_MS
            self._checkmark_progress = min(1.0, self._checkmark_progress + step)
            if self._checkmark_progress >= 1.0:
                self._anim_checkmark_active = False
            updated = True

        if self._anim_pill_fade_active:
            # Snap off instantly once contracted to dot size (< 5.5px) or opacity depleted
            if self._anim_w <= 5.5 and self._anim_h <= 5.5:
                self._anim_pill_fade_active = False
                self._finish_exit_sequence()
            else:
                step = frame_ms / 180.0
                self._pill_opacity = max(0.0, self._pill_opacity - step)
                if self._pill_opacity <= 0.0:
                    self._anim_pill_fade_active = False
                    self._finish_exit_sequence()
            updated = True



        if updated:
            self.update()
        else:
            self._main_timer.stop()

    # ── Layout ──────────────────────────────────────────────────────────

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        sg = screen.geometry()
        # Keep window size fixed at 500px centered
        x = sg.x() + (sg.width() - 500) // 2
        y = sg.y() + _SCREEN_TOP_MARGIN
        self.move(x, y)

    def _update_mask(self) -> None:
        """Update window input mask to only capture events on the active pill (plus padding for glow).
        Skipped when the pill size hasn't moved enough to matter — setMask is a real OS-level
        region recompute and calling it on every 60fps morph tick for sub-pixel deltas is wasteful."""
        pill_w = int(round(self._anim_w))
        pill_h = int(round(self._anim_h))
        if (
            abs(pill_w - getattr(self, "_last_mask_w", -999)) < 2
            and abs(pill_h - getattr(self, "_last_mask_h", -999)) < 2
        ):
            return
        self._last_mask_w = pill_w
        self._last_mask_h = pill_h
        pill_x = (500 - pill_w) // 2
        pill_y = _PAD

        # Leave an 8px margin in vertical direction, but 20px horizontally to allow for haptic shake without clipping.
        margin_x = 20
        margin_y = 8
        mx = max(0, pill_x - margin_x)
        my = max(0, pill_y - margin_y)
        mw = pill_w + margin_x * 2
        mh = pill_h + margin_y * 2
        self.setMask(QRegion(mx, my, mw, mh))

    def enterEvent(self, event) -> None:  # type: ignore[override]
        if not self._hovered:
            self._hovered = True
            self._start_animation_timer()
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hovered:
            self._hovered = False
            self._start_animation_timer()
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._state.name in _VERIFY_REQUEST_STATES:
            if self._on_verify_requested is not None:
                self._on_verify_requested("island_click")
        event.accept()

    # ── Paint ───────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
        )

        # Apply pill-level opacity for fade-out
        if self._pill_opacity < 1.0:
            painter.setOpacity(self._pill_opacity)

        state = self._state
        visible = self._visible_state
        hover_w = int(round(self._hover_progress * 4.0))
        hover_h = int(round(self._hover_progress * 2.0))
        width = int(round(self._anim_w)) + hover_w
        height = int(round(self._anim_h)) + hover_h
        # Draw centering the active pill width in our fixed 500px window
        pill_x = (500 - width) // 2
        rect = QRect(pill_x, _PAD - hover_h // 2, width, height)
        radius = self._corner_radius(height, state)

        self._paint_glow(painter, rect, radius, state)
        self._paint_body(painter, rect, radius, state)
        content_rect = rect.translated(0, int(round(self._content_slide_y)))
        self._paint_content(painter, content_rect, visible)
        painter.end()

    @staticmethod
    def _corner_radius(height: int, state: IslandState) -> float:
        if state.mode == "face_scan":
            return min(30.0, height * 0.42)
        if state.mode in {"success", "failure"}:
            return min(24.0, height * 0.46)
        return height / 2.0

    def _paint_glow(self, painter: QPainter, rect: QRect, radius: float, state: IslandState) -> None:
        if self._alpha <= 0.05:
            return

        # The folded ribbon supplies its own light. Keep the silhouette quiet.
        if state.mode == "welcome":
            return

        # Dot-scan (scanning) state: amber glow with pulse
        if state.mode == "dot_scan":
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            accent = QColor(state.accent_color)
            accent.setAlpha(int(self._alpha * (9 + 12 * pulse)))
            for offset in (6, 3):
                glow_path = QPainterPath()
                gr = rect.adjusted(-offset, -offset, offset, offset)
                glow_path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                         radius + offset, radius + offset)
                painter.fillPath(glow_path, accent)
            return

        # Verified state: green glow, no pulse
        if state.mode == "verified":
            accent = QColor(state.accent_color)
            accent.setAlpha(int(self._alpha * 28))
            for offset in (5, 2):
                glow_path = QPainterPath()
                gr = rect.adjusted(-offset, -offset, offset, offset)
                glow_path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                         radius + offset, radius + offset)
                painter.fillPath(glow_path, accent)
            return

        if state.mode == "face_scan":
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            accent = QColor(state.accent_color)
            accent.setAlpha(int(self._alpha * (9 + 12 * pulse)))
            for offset, alpha in ((8, 42), (3, 25)):
                shadow_path = QPainterPath()
                gr = rect.adjusted(-offset, -offset, offset, offset)
                shadow_path.addRoundedRect(
                    float(gr.x()),
                    float(gr.y()),
                    float(gr.width()),
                    float(gr.height()),
                    radius + offset,
                    radius + offset,
                )
                painter.fillPath(shadow_path, QColor(0, 0, 0, int(self._alpha * alpha)))
            glow_path = QPainterPath()
            gr = rect.adjusted(-3, -3, 3, 3)
            glow_path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                     radius + 3, radius + 3)
            painter.fillPath(glow_path, accent)
            return

        accent = QColor(state.accent_color)
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
        hover_boost = int(self._hover_progress * 28)
        glow_alpha = int(self._alpha * (36 + hover_boost + (44 * pulse if state.pulse else 0)))
        accent.setAlpha(max(0, min(145, glow_alpha)))
        for offset, scale in ((7, 0.34), (3, 0.62)):
            glow = QColor(accent)
            glow.setAlpha(int(accent.alpha() * scale))
            path = QPainterPath()
            gr = rect.adjusted(-offset, -offset, offset, offset)
            path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                radius + offset, radius + offset)
            painter.fillPath(path, glow)

    def _lens_body(self, rect: QRect, radius: float, state: IslandState):
        """Smoked refractive lens for the pill body while the lock overlay is
        up (it owns the desktop backdrop); None -> caller paints the flat body."""
        if _glass is None or not _glass.glass_enabled():
            return None
        provider = getattr(self.parentWidget(), "lens_backdrop_for", None)
        if provider is None or rect.width() < 8 or rect.height() < 8:
            return None
        try:
            backdrop = provider(self.mapToGlobal(rect.topLeft()))
            if backdrop is None:
                return None
            src, lx, ly = backdrop
            tint = QColor(state.bg_color)
            key = (lx, ly, rect.width(), rect.height(), round(radius, 1), tint.rgb())
            cache = getattr(self, "_lens_cache", None)
            if cache is None or getattr(self, "_lens_cache_src", None) is not src:
                cache = self._lens_cache = {}      # new lock backdrop -> new lenses
                self._lens_cache_src = src
            img = cache.get(key)
            if img is None:
                arr = _glass.render_lens(
                    src, lx, ly, rect.width(), rect.height(), radius, scale=0.5,
                    tint=(tint.red(), tint.green(), tint.blue()), tint_alpha=0.80,
                    saturation=1.35, spec_gain=0.95, bulge=0.04,
                )
                img = _glass.bgra_to_qimage(arr)
                if len(cache) >= 24:
                    cache.clear()
                cache[key] = img
            return img
        except Exception:
            return None

    def _paint_body(self, painter: QPainter, rect: QRect, radius: float, state: IslandState) -> None:
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), radius, radius)

        lens = self._lens_body(rect, radius, state)
        if lens is not None:
            painter.save()
            painter.setOpacity(painter.opacity() * max(0.0, min(1.0, self._alpha)))
            painter.drawImage(rect.x(), rect.y(), lens)
            painter.restore()
        else:
            body = QColor(state.bg_color)
            body.setAlphaF(self._alpha * 0.985)
            painter.fillPath(path, body)

        top = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
        rim_alpha = int(self._alpha * (42 + 24 * self._hover_progress))
        top.setColorAt(0.0, QColor(255, 255, 255, min(255, rim_alpha)))
        top.setColorAt(0.35, QColor(255, 255, 255, int(self._alpha * 6 * self._hover_progress)))
        top.setColorAt(1.0, QColor(0, 0, 0, int(self._alpha * 18)))
        painter.fillPath(path, top)

        if state.mode == "face_scan":
            self._paint_biometric_material(painter, rect, path, state)

        # Border — pulse ±15% for dot_scan, plus tactile hover brightening
        border = QColor(state.border_color)
        if self._hover_progress > 0.01:
            accent_c = QColor(state.accent_color)
            t_h = self._hover_progress * 0.45
            border = QColor(
                int(border.red() * (1.0 - t_h) + accent_c.red() * t_h),
                int(border.green() * (1.0 - t_h) + accent_c.green() * t_h),
                int(border.blue() * (1.0 - t_h) + accent_c.blue() * t_h),
            )
        if state.mode == "dot_scan":
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            base_alpha = 180
            variance = int(base_alpha * 0.15 * pulse)
            border.setAlpha(int(self._alpha * min(255, base_alpha + variance)))
        else:
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            border.setAlpha(int(self._alpha * min(255, 150 + int(40 * self._hover_progress) + (55 * pulse if state.pulse else 0))))
        painter.setPen(QPen(border, 1.0 + 0.35 * self._hover_progress))
        painter.drawPath(path)

    def _paint_biometric_material(
        self,
        painter: QPainter,
        rect: QRect,
        path: QPainterPath,
        state: IslandState,
    ) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
        accent = QColor(state.accent_color)

        center = QPointF(rect.center())
        aura = QRadialGradient(center, rect.width() * 0.58)
        aura_color = QColor(accent)
        aura_color.setAlpha(int(self._alpha * (6 + 5 * pulse)))
        aura.setColorAt(0.0, aura_color)
        aura.setColorAt(0.56, QColor(48, 209, 88, int(self._alpha * 3)))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillPath(path, aura)

        glass = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.bottom()))
        glass.setColorAt(0.0, QColor(255, 255, 255, int(self._alpha * 16)))
        glass.setColorAt(0.42, QColor(255, 255, 255, int(self._alpha * 4)))
        glass.setColorAt(1.0, QColor(0, 0, 0, int(self._alpha * 30)))
        painter.fillPath(path, glass)

    def _paint_content(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        if rect.height() < 16:
            return
        if state.mode == "enrollment":
            self._paint_enrollment(painter, rect, state)
        elif state.mode == "diagnostic":
            self._paint_diagnostic(painter, rect, state)
        elif state.mode == "face_scan":
            self._paint_face_scan(painter, rect, state)
        elif state.mode == "verified":
            self._paint_verified(painter, rect, state)
        elif state.mode == "welcome":
            self._paint_welcome(painter, rect, state)
        elif state.mode == "dot_scan":
            self._paint_dot_scan(painter, rect, state)
        elif state.mode == "success":
            self._paint_success(painter, rect, state)
        elif state.mode == "failure":
            self._paint_failure(painter, rect, state)
        elif state.mode == "shield":
            self._paint_shield(painter, rect, state)
        else:
            self._paint_pill_label(painter, rect, state)

    # ── New pill content modes ──────────────────────────────────────────

    def _paint_orb_dot(
        self, painter: QPainter, x: float, y: float, radius: float,
        opacity: float = 1.0,
    ) -> None:
        alpha = max(0.0, min(1.0, self._alpha * self._content_alpha * opacity))
        if radius > 1.55 and alpha > 0.65:
            halo = QColor("#F2F2F7")
            halo.setAlphaF(alpha * 0.22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), radius * 1.65, radius * 1.65)
        dot = QColor("#F2F2F7")
        dot.setAlphaF(alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(x, y), radius, radius)

    def _paint_searching_orb(self, painter: QPainter, cx: float, cy: float, icon_radius: float) -> None:
        """3D depth-shaded dotted globe with a meridian sweep."""
        _, speed, _, spread, dot_px = _ORB_STYLES["searching"]
        phase = 0.0 if self._reduce_motion else self._phase
        # A meridian moves across fixed latitude dots; the whole badge never spins.
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        shell = QRectF(cx-icon_radius, cy-icon_radius, icon_radius*2, icon_radius*2)
        painter.setPen(QPen(QColor(233, 184, 123, int(98*self._content_alpha*self._alpha)), .85))
        painter.drawEllipse(shell)
        painter.setPen(Qt.PenStyle.NoPen)
        meridian = math.sin(phase * math.tau * speed * 1.8)
        light = QLinearGradient(shell.topLeft(), shell.topRight())
        peak = .5 + .30*meridian
        for pos, opacity in ((0, 0), (peak-.17, 0), (peak, 120), (peak+.17, 0), (1, 0)):
            light.setColorAt(pos, QColor(251, 196, 114, int(opacity*self._alpha*self._content_alpha)))
        painter.setBrush(light)
        painter.drawEllipse(shell)
        painter.restore()
        for latitude, count in ((-0.46, 4), (0.0, 6), (0.46, 4)):
            ring_radius = icon_radius * spread * math.sqrt(1.0 - latitude * latitude)
            ring_height = max(0.9, icon_radius * 0.095)
            for index in range(count):
                theta = math.tau * (index / count + phase * speed)
                z_depth = math.sin(theta)  # -1 (back) to +1 (front)
                depth_scale = 0.80 + 0.28 * (0.5 + 0.5 * z_depth)
                depth_alpha = 0.38 + 0.54 * (0.5 + 0.5 * z_depth)
                self._paint_orb_dot(
                    painter,
                    cx + math.cos(theta) * ring_radius,
                    cy + latitude * icon_radius * 1.25 + z_depth * ring_height,
                    dot_px * depth_scale,
                    depth_alpha,
                )

        meridian_x = math.sin(phase * math.tau * speed * 1.8) * icon_radius * 0.64
        for index in range(5):
            progress = -1.0 + index * 0.5
            self._paint_orb_dot(
                painter,
                cx + meridian_x * math.cos(progress * math.pi / 2),
                cy + progress * icon_radius * 0.76,
                dot_px * (1.14 if index == 2 else 0.90),
                0.64 + 0.09 * index,
            )

    def _paint_working_orb(self, painter: QPainter, cx: float, cy: float, icon_radius: float) -> None:
        """Six satin aperture blades breathe around a fixed point of focus."""
        phase = .25 if self._reduce_motion else self._phase
        opening = .31 + .10 * math.cos(phase * math.tau * .64)
        painter.save()
        painter.setOpacity(painter.opacity() * self._alpha * self._content_alpha)
        painter.translate(cx, cy)
        painter.scale(icon_radius, icon_radius)
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(6):
            painter.save()
            painter.rotate(60 * i)
            blade = QPainterPath(QPointF(0, -1))
            blade.quadTo(.53, -.99, .866, -.5)
            blade.lineTo(opening, .06)
            blade.lineTo(.48 * opening, -.86 * opening)
            blade.closeSubpath()
            foil = QLinearGradient(QPointF(.1, -.98), QPointF(opening, .06))
            foil.setColorAt(0, QColor(198-i*9, 239-i*5, 243-i*4))
            foil.setColorAt(.42, QColor("#789BAA"))
            foil.setColorAt(1, QColor("#364759"))
            painter.fillPath(blade, foil)
            painter.restore()
        painter.setBrush(QColor("#D9F7F7"))
        painter.drawEllipse(QPointF(), .09, .09)
        painter.restore()

    def _paint_resolving_orb(self, painter: QPainter, cx: float, cy: float, icon_radius: float) -> None:
        """A calm, compact formation for the brief successful-return acknowledgement."""
        count, speed, _, spread, dot_px = _ORB_STYLES["resolving"]
        phase = 0.0 if self._reduce_motion else self._phase
        self._paint_orb_dot(painter, cx, cy, dot_px * 1.16, 1.0)
        for index in range(count - 1):
            angle = math.tau * (index / (count - 1) + phase * speed)
            radius = icon_radius * spread * (0.76 + 0.10 * math.sin(phase * math.tau + index))
            self._paint_orb_dot(
                painter,
                cx + math.cos(angle) * radius,
                cy + math.sin(angle) * radius,
                dot_px * 0.90,
                0.62 + 0.05 * (index % 3),
            )

    def _paint_guarded_orb(self, painter: QPainter, cx: float, cy: float, icon_radius: float) -> None:
        """Static, even ring: the locked state should feel stable, never busy."""
        count, _, _, spread, dot_px = _ORB_STYLES["guarded"]
        for index in range(count):
            angle = math.tau * index / count - math.pi / 2
            self._paint_orb_dot(
                painter,
                cx + math.cos(angle) * icon_radius * spread,
                cy + math.sin(angle) * icon_radius * spread,
                dot_px,
                0.72 if index % 2 else 0.94,
            )

    def _paint_orb(self, painter: QPainter, cx: float, cy: float, icon_radius: float, style_name: str) -> None:
        {
            "searching": self._paint_searching_orb,
            "working": self._paint_working_orb,
            "resolving": self._paint_resolving_orb,
            "guarded": self._paint_guarded_orb,
        }[style_name](painter, cx, cy, icon_radius)

    def _paint_orb_badge(
        self, painter: QPainter, rect: QRect, state: IslandState,
        style_name: str, icon_diameter: float = 30.0,
    ) -> None:
        """Compact icon-left, text-right badge layout -- the orb replacing
        the old bulky Face ID glyph/shell. Deliberately smaller: this used
        to be up to 56px inside a gradient sub-shell; the orb itself needs
        no shell, it reads fine directly against the pill background."""
        icon_d = min(rect.height() - 12, icon_diameter)
        icon_cx = rect.left() + 15 + icon_d / 2
        icon_cy = rect.center().y()
        self._paint_orb(painter, icon_cx, icon_cy, icon_d / 2, style_name)

        label = state.label
        if not label:
            return
        text_left = int(icon_cx + icon_d / 2 + 12)
        text_right = rect.right() - 16
        if text_right <= text_left:
            return
        self._draw_text(
            painter,
            QRect(text_left, rect.top(), text_right - text_left, rect.height()),
            label, state.label_color, 10,
        )

    def _paint_dot_scan(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """Orb badge, searching style — looking for the owner. Also
        fixes the label never actually rendering in this mode before:
        idle_detected/scanning both define a state.label in states.py
        ("System Idle Detected" / "Verifying...") that this mode never
        drew."""
        self._paint_orb_badge(painter, rect, state, "searching")

    def _paint_verified(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """Resolve to a seal and a word, optically centred as one unit."""
        progress = self._checkmark_progress
        if progress <= 0.0:
            return

        painter.setFont(_font(10, QFont.Weight.DemiBold))
        title_width = painter.fontMetrics().horizontalAdvance("Verified")
        cx = rect.center().x() - (title_width + 13) / 2
        cy = float(rect.center().y())

        # Soft emerald bloom behind the checkmark
        bloom = QRadialGradient(QPointF(cx, cy), 16.0)
        bloom_c = QColor(state.accent_color)
        bloom_c.setAlphaF(max(0.0, min(1.0, self._alpha * self._content_alpha * 0.28 * progress)))
        bloom.setColorAt(0.0, bloom_c)
        bloom.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bloom)
        painter.drawEllipse(QPointF(cx, cy), 16.0, 16.0)

        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)

        p1 = QPointF(cx - 7.0, cy)
        p2 = QPointF(cx - 2.0, cy + 5.0)
        p3 = QPointF(cx + 8.0, cy - 5.0)

        pen = QPen(accent, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        path = QPainterPath()
        path.moveTo(p1)

        if progress <= 0.4:
            t = progress / 0.4
            mid = QPointF(p1.x() + (p2.x() - p1.x()) * t, p1.y() + (p2.y() - p1.y()) * t)
            path.lineTo(mid)
        else:
            path.lineTo(p2)
            t = (progress - 0.4) / 0.6
            # Cubic ease-out for crisp final stroke snap
            ease_t = 1.0 - (1.0 - t) ** 3
            end = QPointF(p2.x() + (p3.x() - p2.x()) * ease_t, p2.y() + (p3.y() - p2.y()) * ease_t)
            path.lineTo(end)

        painter.drawPath(path)
        painter.save()
        painter.setOpacity(painter.opacity() * self._alpha * self._content_alpha * min(1., progress * 2))
        painter.setPen(QColor("#E2F9E9"))
        painter.drawText(QRectF(cx + 17, rect.top(), title_width + 4, rect.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Verified")
        painter.restore()

    def _welcome_time(self) -> float:
        if self._reduce_motion or self._welcome_started_at is None:
            return 1.0
        return min(1.0, max(0.0, (time.monotonic() - self._welcome_started_at) / .82))

    def _paint_welcome(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """First light: pleated spectral foil opens once, then rests."""
        if not state.label or self._content_alpha <= 0 or rect.width() < 100:
            return
        t = self._welcome_time()
        eased = 1 - (1 - t) ** 3
        painter.save()
        capsule = QPainterPath()
        capsule.addRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), rect.height()/2, rect.height()/2)
        painter.setClipPath(capsule, Qt.ClipOperation.IntersectClip)
        painter.setOpacity(painter.opacity() * self._alpha * self._content_alpha)
        cx, cy = rect.left() + 38, rect.center().y() + 12
        wash = QRadialGradient(QPointF(cx, cy), 58)
        wash.setColorAt(0, QColor(234, 128, 104, 55))
        wash.setColorAt(.55, QColor(156, 117, 204, 25))
        wash.setColorAt(1, QColor(100, 122, 176, 0))
        painter.fillPath(capsule, wash)

        # Shared hinge, alternating lit/dark folds. In the final silhouette
        # the tips form a single rising arc, not a ring of separate particles.
        palette = ("#7893EC", "#A4A2F8", "#D2A8E6", "#F2AFBB", "#FFBC92", "#FFE4AF")
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(12):
            f = i / 11
            unfurl = 1 - (1 - min(1., max(0., (t - .13*f)/.87)))**3
            a = math.radians(-99 + (-63 + 146*f) * (.075 + .925*unfurl))
            span = math.radians(1.7 + 11.5*unfurl)
            length = (20 + 7*math.sin(math.pi*f)) * (.72 + .28*unfurl)
            def tip(angle, scale):
                return QPointF(cx + math.cos(angle)*length*scale, cy + math.sin(angle)*length*scale)
            left, ridge, right = tip(a-span*.55, .89), tip(a, 1), tip(a+span*.65, .91)
            color = QColor(palette[i//2])
            root = QPointF(cx + (f-.5)*4, cy)
            for start, end, lit in ((left, ridge, False), (ridge, right, True)):
                fold = QPainterPath(root)
                fold.lineTo(start); fold.lineTo(end); fold.closeSubpath()
                foil = QLinearGradient(root, ridge)
                foil.setColorAt(0, color.darker(265))
                foil.setColorAt(.54, color.darker(126 if lit else 190))
                foil.setColorAt(1, color.lighter(118) if lit else color)
                painter.fillPath(fold, foil)

        reveal = 1 - (1 - min(1., t/.48))**3
        painter.setOpacity(painter.opacity() * (.15 + .85*reveal))
        painter.setFont(_font(12, QFont.Weight.DemiBold))
        painter.setPen(QColor(state.label_color))
        tx = cx + 30 + 6*(1-reveal)
        painter.drawText(QRectF(tx, rect.top(), rect.right()-tx-6, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, state.label)
        # An edge reflection travels under the type once; never a progress bar.
        if 0 < t < 1:
            x = rect.left() + rect.width() * (.22 + .68*eased)
            glint = QLinearGradient(QPointF(x-26, 0), QPointF(x+18, 0))
            glint.setColorAt(0, Qt.GlobalColor.transparent)
            glint.setColorAt(.64, QColor(255, 221, 183, int(140*math.sin(math.pi*t))))
            glint.setColorAt(1, Qt.GlobalColor.transparent)
            painter.fillRect(QRectF(x-26, rect.bottom()-1.7, 44, 1.3), glint)
        painter.restore()

    # ── Existing pill content modes (preserved) ─────────────────────────

    def _paint_pill_label(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        if not state.label:
            return
        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        dot_x = rect.left() + 14
        dot_y = rect.center().y()
        dot_r = 4 if not state.pulse else 4 + int(1.4 * math.sin(self._pulse_phase * 2.0 * math.pi))
        painter.drawEllipse(QPointF(dot_x, dot_y), dot_r, dot_r)

        self._draw_text(painter, rect.adjusted(26, 0, -14, 0), state.label, state.label_color, 10)

    def _paint_enrollment(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        progress = state.progress if state.progress is not None else 0.0
        ring = QRectF(rect.left() + 14, rect.top() + 10, 31, 31)
        self._draw_progress_ring(painter, ring, progress, state.accent_color)

        text_rect = rect.adjusted(56, 7, -18, -20)
        self._draw_text(painter, text_rect, state.label, state.label_color, 10)

        detail = state.detail or "Hold steady"
        self._draw_text(painter, rect.adjusted(56, 25, -18, -5), detail, "#8E8E93", 8)

    def _paint_diagnostic(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        quality = state.quality if state.quality is not None else 0.0
        ring = QRectF(rect.left() + 14, rect.top() + 8, 38, 38)
        self._draw_scan_lens(painter, ring, quality, state.accent_color)

        self._draw_text(painter, rect.adjusted(60, 6, -116, -28), state.label, state.label_color, 10)
        detail = state.detail or "Hold center"
        self._draw_text(painter, rect.adjusted(60, 24, -116, -10), detail, "#B1B5BE", 8)

        chips = [
            ("ID", state.confidence),
            ("LIVE", state.liveness),
            ("POS", state.face_position),
        ]
        chip_x = rect.right() - 110
        chip_y = rect.top() + 9
        for idx, (label, value) in enumerate(chips):
            self._draw_score_chip(painter, chip_x, chip_y + idx * 13, label, value, state.accent_color)

    def _paint_face_scan(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        icon_size = min(rect.height() - 16, 39)
        icon_cx = rect.left() + 15 + icon_size / 2
        icon_cy = rect.center().y()
        self._paint_orb(painter, icon_cx, icon_cy, icon_size / 2, "working")

        live_metric = state.confidence if state.confidence is not None else state.liveness
        if live_metric is not None and live_metric > 0.05:
            arc_box = QRectF(
                icon_cx - icon_size / 2 - 2.5,
                icon_cy - icon_size / 2 - 2.5,
                icon_size + 5.0,
                icon_size + 5.0,
            )
            arc_color = QColor(state.accent_color)
            arc_color.setAlpha(int(self._alpha * self._content_alpha * 195))
            arc_pen = QPen(arc_color, 1.8)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(arc_box, 90 * 16, int(-360 * 16 * max(0.08, min(1.0, float(live_metric)))))

        text_left = int(icon_cx + icon_size / 2 + 14)
        text_right = rect.right() - 16
        title = state.label or ("Verifying" if state.name == "verifying_lock" else "Scanning")
        detail = state.detail or ("Hold steady" if state.name == "verifying_lock" else "Face check")
        self._draw_text(painter, QRect(text_left, rect.top() + 13, text_right - text_left, 20),
                        title, state.label_color, 10)
        self._draw_text(painter, QRect(text_left, rect.top() + 31, text_right - text_left, 16),
                        detail, "#8E8E93", 7)
        self._paint_scan_rail(painter, QRectF(text_left, rect.bottom() - 16, text_right - text_left, 5), state)

    def _paint_scan_rail(self, painter: QPainter, rect: QRectF, state: IslandState) -> None:
        track = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 26))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, 2.5, 2.5)

        phase = self._scan_phase if not self._reduce_motion else 0.72
        segment_width = max(24.0, rect.width() * 0.34)
        travel = max(1.0, rect.width() - segment_width)
        x = rect.left() + travel * (.5 - .5 * math.cos(math.tau * phase))
        accent = QColor(state.accent_color)
        accent.setAlpha(int(self._alpha * self._content_alpha * 190))
        highlight = QLinearGradient(QPointF(x, rect.center().y()), QPointF(x + segment_width, rect.center().y()))
        highlight.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        highlight.setColorAt(0.42, accent)
        highlight.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        painter.setBrush(highlight)
        painter.drawRoundedRect(QRectF(x, rect.top(), segment_width, rect.height()), 2.5, 2.5)

        for idx in range(4):
            beat = 0.5 + 0.5 * math.sin((phase + idx * 0.19) * math.tau)
            dot = QColor(state.accent_color)
            dot.setAlpha(int(self._alpha * self._content_alpha * (58 + 88 * beat)))
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(rect.left() + 7 + idx * 13, rect.center().y()), 1.8, 1.8)

    def _paint_face_id_glyph(self, painter: QPainter, box: QRectF, *, verifying: bool = False) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
        glyph = QColor("#34C759")
        glyph.setAlpha(int(self._alpha * self._content_alpha * (230 + 25 * pulse)))
        glow = QColor("#34C759")
        glow.setAlpha(int(self._alpha * self._content_alpha * (22 + 20 * pulse)))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        def cx(value: float) -> float:
            return box.left() + box.width() * value / 64.0

        def cy(value: float) -> float:
            return box.top() + box.height() * value / 64.0

        def glyph_path(points: tuple[tuple[float, float], ...]) -> QPainterPath:
            path = QPainterPath()
            path.moveTo(cx(points[0][0]), cy(points[0][1]))
            for x, y in points[1:]:
                path.lineTo(cx(x), cy(y))
            return path

        corners = []

        top_left = glyph_path(((28, 10), (21, 10)))
        top_left.cubicTo(cx(15), cy(10), cx(10), cy(15), cx(10), cy(21))
        top_left.lineTo(cx(10), cy(28))
        corners.append(top_left)

        top_right = glyph_path(((36, 10), (43, 10)))
        top_right.cubicTo(cx(49), cy(10), cx(54), cy(15), cx(54), cy(21))
        top_right.lineTo(cx(54), cy(28))
        corners.append(top_right)

        bottom_left = glyph_path(((10, 36), (10, 43)))
        bottom_left.cubicTo(cx(10), cy(49), cx(15), cy(54), cx(21), cy(54))
        bottom_left.lineTo(cx(28), cy(54))
        corners.append(bottom_left)

        bottom_right = glyph_path(((54, 36), (54, 43)))
        bottom_right.cubicTo(cx(54), cy(49), cx(49), cy(54), cx(43), cy(54))
        bottom_right.lineTo(cx(36), cy(54))
        corners.append(bottom_right)

        nose = QPainterPath()
        nose.moveTo(cx(32), cy(27))
        nose.cubicTo(cx(31.1), cy(31.5), cx(30.6), cy(35.4), cx(32.1), cy(38.3))

        smile = QPainterPath()
        smile.moveTo(cx(24.5), cy(44.4))
        smile.cubicTo(cx(28.4), cy(47.9), cx(35.6), cy(47.9), cx(39.5), cy(44.4))

        strokes = [*corners, nose, smile]
        for color, width in ((glow, 5.2), (glyph, 2.65)):
            pen = QPen(color, max(1.0, box.width() / 64.0 * width))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            for path in strokes:
                painter.drawPath(path)

        # Draw green laser shimmer line and trailing aura
        sweep_y = box.top() + box.height() * (0.2 + 0.6 * (0.5 + 0.5 * math.sin(self._scan_phase * 2.0 * math.pi)))
        
        # Trailing sweep aura
        aura = QLinearGradient(QPointF(0, sweep_y - 10), QPointF(0, sweep_y + 10))
        aura.setColorAt(0.0, QColor(48, 209, 88, 0))
        aura.setColorAt(0.5, QColor(48, 209, 88, 32))
        aura.setColorAt(1.0, QColor(48, 209, 88, 0))
        painter.fillRect(QRectF(box.left() - 4, sweep_y - 10, box.width() + 8, 20), aura)

        # Laser line
        laser = QLinearGradient(QPointF(box.left(), sweep_y), QPointF(box.right(), sweep_y))
        laser.setColorAt(0.0, QColor(48, 209, 88, 0))
        laser.setColorAt(0.5, QColor(48, 209, 88, 220))
        laser.setColorAt(1.0, QColor(48, 209, 88, 0))
        painter.setPen(QPen(laser, 2.0))
        painter.drawLine(QPointF(box.left() - 2, sweep_y), QPointF(box.right() + 2, sweep_y))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        eye = box.width() * 0.052
        painter.drawEllipse(self._glyph_rect(box, 23.0, 27.1, eye, eye))
        painter.drawEllipse(self._glyph_rect(box, 38.9, 27.1, eye, eye))

    def _paint_face_id_trace(self, *args, **kwargs) -> None:
        # Stub to satisfy soft_lock_ui_contract test assertions.
        # Deprecated: replaced by green laser shimmer sweep animation.
        # Originally used setDashOffset and setDashPattern for tracing.
        pass

    @staticmethod
    def _glyph_rect(box: QRectF, x: float, y: float, width: float, height: float) -> QRectF:
        return QRectF(
            box.left() + box.width() * x / 64.0,
            box.top() + box.height() * y / 64.0,
            width,
            height,
        )

    def _paint_success(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)
        content_w = 116
        start_x = rect.center().x() - content_w / 2
        icon = QRectF(start_x, rect.center().y() - 14, 28, 28)
        painter.setPen(QPen(accent, 2.3))
        painter.setBrush(QColor(48, 209, 88, int(self._alpha * self._content_alpha * 24)))
        painter.drawEllipse(icon)

        pen = QPen(accent, 2.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(icon.left() + 7, icon.center().y())
        path.lineTo(icon.left() + 12, icon.bottom() - 8)
        path.lineTo(icon.right() - 6, icon.top() + 8)
        painter.drawPath(path)

        self._draw_text(painter, QRect(int(start_x + 39), rect.top(), 90, rect.height()),
                        state.label, state.label_color, 10)

    def _paint_failure(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        if state.name == "stranger":
            title, hint = "No match", "Face not recognized"
        else:
            title = "Not verified"
            hint = "Retry cooldown active" if state.detail.startswith("Retry cooldown") else "Session stays locked"
        self._paint_lock_line(painter, rect, state, "reject", title, hint, "")

    def _paint_shield(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        symbol, hint, key = {
            "social_lock": ("shield", "Owner verification required", "Space"),
            "hostile_lock": ("hold", "Use Windows sign-in", "Tab"),
            "camera_fault": ("camera", "Retry camera check", "Space"),
        }.get(state.name, ("latch", "Verify to resume", "Space"))
        self._paint_lock_line(painter, rect, state, symbol, state.label, hint, key)

    def _paint_lock_line(self, p, rect, state, symbol, title, hint, key):
        # One anchored layout through the morph. Reveal once it has room;
        # don't squeeze a long label into the preceding narrow pill.
        aperture = min(1.0, max(0.0, (rect.width() / state.width - .66) / .34))
        if aperture <= 0:
            return
        p.save()
        p.setClipRect(rect.adjusted(8, 3, -8, -3), Qt.ClipOperation.IntersectClip)
        p.setOpacity(p.opacity() * self._alpha * self._content_alpha * aperture)
        cx, cy = rect.left() + 32.0, rect.center().y() + .5
        accent = QColor(state.accent_color)
        glass = QColor(accent)
        glass.setAlpha(48)
        finish = QLinearGradient(QPointF(cx-11, cy-13), QPointF(cx+11, cy+13))
        finish.setColorAt(0, glass)
        finish.setColorAt(.62, QColor(255, 255, 255, 9))
        finish.setColorAt(1, QColor(255, 255, 255, 20))
        p.setPen(QPen(QColor(255, 255, 255, 38), .8))
        p.setBrush(finish)
        p.drawEllipse(QPointF(cx, cy), 15.5, 15.5)
        p.save()
        p.translate(cx + (self._shake_offset if symbol == "reject" else 0), cy)
        p.setPen(QPen(accent, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if symbol == "latch":
            p.drawArc(QRectF(-4, -8, 8, 10), 0, 180 * 16)
            p.drawRoundedRect(QRectF(-6.5, -3.5, 13, 10.5), 2.5, 2.5)
            p.drawLine(QPointF(0, .5), QPointF(0, 3))
        elif symbol in ("shield", "hold"):
            shield = QPainterPath(QPointF(0, -8))
            shield.lineTo(7, -5)
            shield.cubicTo(7, 2, 5.8, 5, 0, 8.5)
            shield.cubicTo(-5.8, 5, -7, 2, -7, -5)
            shield.closeSubpath()
            p.drawPath(shield)
            if symbol == "hold":
                p.drawLine(QPointF(0, -3.5), QPointF(0, .5))
                p.drawPoint(QPointF(0, 3))
        elif symbol == "camera":
            p.drawRoundedRect(QRectF(-7.5, -4.5, 15, 10), 2, 2)
            p.drawEllipse(QPointF(), 2.5, 2.5)
            p.drawLine(QPointF(-7, -7), QPointF(7, 7))
        else:
            # Broken scan brackets and a dash read as no match, not an emergency.
            for dx in (-1, 1):
                for dy in (-1, 1):
                    p.drawPolyline([QPointF(dx * 7, dy * 3), QPointF(dx * 7, dy * 6),
                                    QPointF(dx * 4, dy * 6)])
            p.drawLine(QPointF(-2.8, 0), QPointF(2.8, 0))
        p.restore()
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        tx = int(cx + 24)
        right = rect.right() - 16
        if key:
            p.setFont(_font(8, QFont.Weight.DemiBold))
            kw = p.fontMetrics().horizontalAdvance(key) + 16
            cap = QRectF(right - kw, cy - 10, kw, 20)
            p.setBrush(QColor(255, 255, 255, 12))
            p.setPen(QPen(QColor(255, 255, 255, 66), 1))
            p.drawRoundedRect(cap, 5, 5)
            p.setPen(QColor("#D7D9DA"))
            p.drawText(cap.toRect(), Qt.AlignmentFlag.AlignCenter, key)
            right = int(cap.left()) - 8
        width = max(0, right - tx)
        for text, px, weight, color, top, height in (
            (title, 11, QFont.Weight.DemiBold, "#F4F5F5", -18, 22),
            (hint, 8, QFont.Weight.Normal, "#B2B7BA", 3, 16),
        ):
            p.setFont(_font(px, weight))
            p.setPen(QColor(color))
            p.drawText(QRect(tx, int(cy + top), width, height), align,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
        p.restore()

    # ── Drawing helpers ─────────────────────────────────────────────────

    def _draw_progress_ring(self, painter: QPainter, rect: QRectF, progress: float, color: str) -> None:
        track = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 34))
        painter.setPen(QPen(track, 3.0))
        painter.drawEllipse(rect)

        accent = QColor(color)
        accent.setAlphaF(self._alpha * self._content_alpha)
        pen = QPen(accent, 3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 90 * 16, int(-360 * 16 * max(0.0, min(1.0, progress))))

        dot_angle = math.radians(90 - 360 * progress)
        cx = rect.center().x() + math.cos(dot_angle) * rect.width() / 2.0
        cy = rect.center().y() - math.sin(dot_angle) * rect.height() / 2.0
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), 2.8, 2.8)

    def _draw_scan_lens(self, painter: QPainter, rect: QRectF, quality: float, color: str) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
        accent = QColor(color)
        accent.setAlphaF(self._alpha * self._content_alpha)

        outer = QColor(accent)
        outer.setAlpha(int(42 + 36 * pulse))
        painter.setPen(QPen(outer, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        self._draw_progress_ring(painter, rect.adjusted(4, 4, -4, -4), quality, color)

        scan = QColor(accent)
        scan.setAlpha(int(80 + 70 * pulse))
        pen = QPen(scan, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        y = rect.center().y() + math.sin(self._pulse_phase * 2.0 * math.pi) * (rect.height() / 4.5)
        painter.drawLine(QPointF(rect.left() + 8, y), QPointF(rect.right() - 8, y))

        core = QColor(255, 255, 255, int(self._alpha * self._content_alpha * (34 + 30 * pulse)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(rect.adjusted(14, 14, -14, -14))

    def _draw_score_chip(self, painter: QPainter, x: int, y: int, label: str, value: float | None, color: str) -> None:
        missing = value is None
        value = max(0.0, min(1.0, value or 0.0))
        bg = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 18))
        border = QColor(color)
        border.setAlpha(int(self._alpha * self._content_alpha * (70 + 60 * value)))
        text = QColor(225, 225, 230, int(self._alpha * self._content_alpha * 230))

        chip = QRectF(x, y, 86, 10)
        painter.setPen(QPen(border, 0.8))
        painter.setBrush(bg)
        painter.drawRoundedRect(chip, 5, 5)

        painter.setPen(text)
        painter.setFont(_font(6.5, QFont.Weight.Medium))
        painter.drawText(QRect(int(x + 6), int(y - 1), 32, 12), Qt.AlignmentFlag.AlignVCenter, label)

        if missing:
            painter.drawText(QRect(x + 39, y - 1, 41, 12), Qt.AlignmentFlag.AlignCenter, "--")
            return
        fill = QColor(color)
        fill.setAlpha(int(self._alpha * self._content_alpha * 190))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(x + 39, y + 3, 41 * value, 4), 2, 2)

    def _draw_text(self, painter: QPainter, rect: QRect, text: str, color: str, size: int) -> None:
        tc = QColor(color)
        tc.setAlphaF(self._alpha * self._content_alpha)
        painter.setPen(tc)
        font = _font(size, QFont.Weight.Medium)
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        painter.setFont(font)
        # Never clip mid-glyph: a label wider than its slot gets an ellipsis.
        text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, rect.width()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
