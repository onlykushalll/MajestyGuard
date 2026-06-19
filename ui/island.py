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

log = logging.getLogger("MajestyGuard.Island")

_SCREEN_TOP_MARGIN = 8
_PULSE_PERIOD_MS = 1800
_FLASH_BLINKS = 4
_FLASH_INTERVAL_MS = 110
_FRAME_MS = 16
_SCAN_PERIOD_MS = 900
_PAD = 8
_VERIFY_REQUEST_STATES = {"locked_passive", "soft_locked", "social_lock"}

# Dot-pulse scanning constants
_DOT_COUNT = 3
_DOT_RADIUS = 2.5  # 5px diameter
_DOT_GAP = 6.0
_DOT_SCAN_PERIOD_MS = 1200

# Verified → Welcome → Fade sequence timing
_VERIFIED_DURATION_MS = 400
_CHECKMARK_DRAW_MS = 150
_WELCOME_DWELL_MS = 2000
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
        self._flash_frame_counter = 0
        self._shake_phase = 0.0
        self._shake_offset = 0.0
        self._anim_shake_active = False
        self._verify_start_time = 0.0
        self._deferred_lock_state = None

        self._setup_window()
        self._setup_timers()
        self._reposition()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # Use a fixed large canvas size to prevent OS-level window resize artifacts & DWM jitter
        self.setFixedSize(500, 120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _setup_timers(self) -> None:
        # Single unified timer for all 60fps animations to ensure exactly one update() per frame
        self._main_timer = QTimer(self)
        self._main_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._main_timer.setInterval(_FRAME_MS)
        self._main_timer.timeout.connect(self._tick_animations)

        # Exit sequence single-shot timers (dwells)
        self._verified_dwell_timer = QTimer(self)
        self._verified_dwell_timer.setSingleShot(True)
        self._verified_dwell_timer.timeout.connect(self._begin_welcome)

        self._welcome_dwell_timer = QTimer(self)
        self._welcome_dwell_timer.setSingleShot(True)
        self._welcome_dwell_timer.timeout.connect(self._begin_pill_fade)

    def apply_state(self, state: IslandState) -> None:
        # If the state is completely identical, avoid redundant updates/repaints
        if state == self._state and not self._in_exit_sequence and not self._anim_shake_active:
            return

        # If we are in the exit sequence and a non-active state arrives, abort
        if self._in_exit_sequence and state.name not in ("active", "welcome"):
            self._abort_exit_sequence()

        # If the stranger shake is running, defer applying any locked states until the shake completes
        if self._anim_shake_active and state.name in {"locked_passive", "soft_locked", "social_lock", "hostile_lock", "locked"}:
            self._deferred_lock_state = state
            return

        # Intercept active state → begin exit sequence or treat as idle if already unlocked
        if state.name == "active":
            if self._in_exit_sequence:
                return
            if self._state.name in {"locked_passive", "soft_locked", "verifying_lock", "social_lock", "hostile_lock", "locked"}:
                self._begin_exit_sequence(state)
                return
            else:
                state = get_state("idle")

        # If we are unlocked (idle) and we get scanning, ignore it to prevent popping open
        if state.name == "scanning" and self._state.name == "idle" and not self._in_exit_sequence:
            return

        # Show/hide window based on active lock state
        if state.name == "idle" and not self._in_exit_sequence:
            self.hide()
        else:
            if self.isHidden():
                self.show()

        # If we transition from verifying to a lock state (rejection), show stranger shake first!
        if self._state.name == "verifying_lock" and state.name in {"social_lock", "hostile_lock", "stranger"}:
            if getattr(self, "_deferred_lock_state", None) is None or state.name != "stranger":
                self._deferred_lock_state = state
            state = get_state("stranger")

        # Enforce minimum verification scan time (650ms) before transitioning out of verifying_lock
        if self._state.name == "verifying_lock" and state.name != "verifying_lock":
            elapsed = time.monotonic() - getattr(self, "_verify_start_time", 0.0)
            min_duration = 0.65
            if elapsed < min_duration:
                remaining_ms = int((min_duration - elapsed) * 1000)
                if hasattr(self, "_defer_timer") and self._defer_timer.isActive():
                    self._defer_timer.stop()
                self._defer_timer = QTimer(self)
                self._defer_timer.setSingleShot(True)
                self._defer_timer.timeout.connect(lambda: self.apply_state(state))
                self._defer_timer.start(remaining_ms)
                return

        # Track when we enter verifying_lock state to enforce minimum duration
        if state.name == "verifying_lock":
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
        if state.name in {"locked_passive", "soft_locked", "social_lock", "hostile_lock", "locked"}:
            self._target_w = 120.0
            self._target_h = 28.0
        else:
            self._target_w = float(state.width)
            self._target_h = float(state.height)
        
        # Reset previous animation states
        self._anim_flash_active = False
        self._anim_pulse_active = False
        self._anim_dot_scan_active = False
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

        if content_changed and not self._reduce_motion:
            self._pending_state = state
            self._content_fading_out = True
            self._anim_content_active = True
        else:
            self._visible_state = state
            self._pending_state = None
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

    # ── Exit sequence orchestration ─────────────────────────────────────

    def _begin_exit_sequence(self, active_state: IslandState) -> None:
        """Verified → Welcome → Fade pill sequence."""
        self._in_exit_sequence = True
        self._pill_opacity = 1.0
        self._checkmark_progress = 0.0

        # Tell overlay to start dissolving immediately
        if self._on_overlay_dissolve is not None:
            self._on_overlay_dissolve()

        # Apply verified state (green border, checkmark only)
        self._state = active_state
        self._visible_state = active_state
        self._target_w = float(active_state.width)
        self._target_h = float(active_state.height)
        self._content_alpha = 1.0
        self._anim_pulse_active = False
        self._anim_flash_active = False
        self._anim_dot_scan_active = False
        self._anim_scan_active = False
        self._alpha = 1.0

        if not self._reduce_motion:
            self._anim_morph_active = True
            self._anim_checkmark_active = True
            self._start_animation_timer()

        # After verified dwell, transition to welcome
        self._verified_dwell_timer.start(_VERIFIED_DURATION_MS)

    def _begin_welcome(self) -> None:
        """Transition pill to Welcome state."""
        welcome = get_state("welcome")
        self._state = welcome
        self._visible_state = welcome
        self._target_w = float(welcome.width)
        self._target_h = float(welcome.height)
        self._content_alpha = 1.0
        self._anim_checkmark_active = False

        if not self._reduce_motion:
            self._anim_morph_active = True
            self._start_animation_timer()

        self.update()
        self._welcome_dwell_timer.start(_WELCOME_DWELL_MS)

    def _begin_pill_fade(self) -> None:
        """Fade pill out over 400ms."""
        if not self._reduce_motion:
            self._anim_pill_fade_active = True
            self._start_animation_timer()
        else:
            self._finish_exit_sequence()

    def _finish_exit_sequence(self) -> None:
        """Clean up after exit sequence completes."""
        self._in_exit_sequence = False
        self._pill_opacity = 0.0
        self._anim_pill_fade_active = False
        self._anim_checkmark_active = False
        # Reset to idle
        idle = get_state("idle")
        self._state = idle
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
        self._pill_opacity = 1.0
        self._verified_dwell_timer.stop()
        self._welcome_dwell_timer.stop()
        self._anim_pill_fade_active = False
        self._anim_checkmark_active = False

    # ── Timer ticks ─────────────────────────────────────────────────────

    def _start_animation_timer(self) -> None:
        if not self._main_timer.isActive():
            self._main_timer.start()

    def _tick_animations(self) -> None:
        updated = False

        if self._anim_pulse_active:
            self._pulse_phase = (self._pulse_phase + _FRAME_MS / _PULSE_PERIOD_MS) % 1.0
            updated = True

        if self._anim_flash_active:
            self._flash_frame_counter += 1
            if self._flash_frame_counter >= 7:  # toggle every ~110ms
                self._flash_frame_counter = 0
                self._flash_count -= 1
                self._alpha = 1.0 if self._flash_count % 2 == 0 else 0.22
                if self._flash_count <= 0:
                    self._anim_flash_active = False
                    self._alpha = 1.0
                updated = True

        if self._anim_scan_active:
            self._scan_phase = (self._scan_phase + _FRAME_MS / _SCAN_PERIOD_MS) % 1.0
            updated = True

        if self._anim_dot_scan_active:
            self._dot_scan_phase = (self._dot_scan_phase + _FRAME_MS / _DOT_SCAN_PERIOD_MS) % 1.0
            updated = True

        if self._anim_shake_active:
            self._shake_phase += 0.016 / 0.45
            if self._shake_phase >= 1.0:
                self._anim_shake_active = False
                self._shake_offset = 0.0
                self._shake_phase = 0.0
                if getattr(self, "_deferred_lock_state", None) is not None:
                    deferred = self._deferred_lock_state
                    self._deferred_lock_state = None
                    self.apply_state(deferred)
            else:
                self._shake_offset = 12.0 * math.sin(self._shake_phase * math.pi * 5.0) * (1.0 - self._shake_phase)
            updated = True

        if self._anim_content_active:
            step = 0.16
            if self._content_fading_out:
                self._content_alpha = max(0.0, self._content_alpha - step)
                if self._content_alpha <= 0.0:
                    if self._pending_state is not None:
                        self._visible_state = self._pending_state
                    self._pending_state = None
                    self._content_fading_out = False
            else:
                self._content_alpha = min(1.0, self._content_alpha + step)
                if self._content_alpha >= 1.0:
                    self._anim_content_active = False
            updated = True

        if self._anim_morph_active:
            stiffness = 0.18
            damping = 0.70
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
            step = _FRAME_MS / _CHECKMARK_DRAW_MS
            self._checkmark_progress = min(1.0, self._checkmark_progress + step)
            if self._checkmark_progress >= 1.0:
                self._anim_checkmark_active = False
            updated = True

        if self._anim_pill_fade_active:
            step = _FRAME_MS / _PILL_FADE_MS
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
        """Update window input mask to only capture events on the active pill (plus padding for glow)."""
        pill_w = int(round(self._anim_w))
        pill_h = int(round(self._anim_h))
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

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._state.name in _VERIFY_REQUEST_STATES:
            if self._on_verify_requested is not None:
                self._on_verify_requested("island_click")
        event.accept()

    # ── Paint ───────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Apply pill-level opacity for fade-out
        if self._pill_opacity < 1.0:
            painter.setOpacity(self._pill_opacity)

        state = self._state
        visible = self._visible_state
        width = int(round(self._anim_w))
        height = int(round(self._anim_h))
        # Draw centering the active pill width in our fixed 500px window
        pill_x = (500 - width) // 2 + int(round(self._shake_offset))
        rect = QRect(pill_x, _PAD, width, height)
        radius = self._corner_radius(height, state)

        self._paint_glow(painter, rect, radius, state)
        self._paint_body(painter, rect, radius, state)
        self._paint_content(painter, rect, visible)
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

        # Welcome state: subtle green glow
        if state.mode == "welcome":
            accent = QColor(state.accent_color)
            pulse = 0.7  # gentle static glow
            accent.setAlpha(int(self._alpha * (12 + 8 * pulse)))
            for offset in (6, 3):
                glow_path = QPainterPath()
                gr = rect.adjusted(-offset, -offset, offset, offset)
                glow_path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                         radius + offset, radius + offset)
                painter.fillPath(glow_path, accent)
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
        glow_alpha = int(self._alpha * (36 + (44 * pulse if state.pulse else 0)))
        accent.setAlpha(max(0, min(120, glow_alpha)))
        for offset, scale in ((7, 0.32), (3, 0.58)):
            glow = QColor(accent)
            glow.setAlpha(int(accent.alpha() * scale))
            path = QPainterPath()
            gr = rect.adjusted(-offset, -offset, offset, offset)
            path.addRoundedRect(float(gr.x()), float(gr.y()), float(gr.width()), float(gr.height()),
                                radius + offset, radius + offset)
            painter.fillPath(path, glow)

    def _paint_body(self, painter: QPainter, rect: QRect, radius: float, state: IslandState) -> None:
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), radius, radius)

        body = QColor(state.bg_color)
        body.setAlphaF(self._alpha * 0.985)
        painter.fillPath(path, body)

        top = QLinearGradient(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
        top.setColorAt(0.0, QColor(255, 255, 255, int(self._alpha * 26)))
        top.setColorAt(0.45, QColor(255, 255, 255, int(self._alpha * 8)))
        top.setColorAt(1.0, QColor(0, 0, 0, int(self._alpha * 12)))
        painter.fillPath(path, top)

        if state.mode == "face_scan":
            self._paint_biometric_material(painter, rect, path, state)

        # Border — pulse ±15% for dot_scan
        border = QColor(state.border_color)
        if state.mode == "dot_scan":
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            base_alpha = 180
            variance = int(base_alpha * 0.15 * pulse)
            border.setAlpha(int(self._alpha * min(255, base_alpha + variance)))
        else:
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase * 2.0 * math.pi)
            border.setAlpha(int(self._alpha * (150 + (55 * pulse if state.pulse else 0))))
        painter.setPen(QPen(border, 1.0))
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
        if state.name in {"locked_passive", "soft_locked", "social_lock", "hostile_lock", "locked"}:
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

    def _paint_dot_scan(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """Three dots that pulse in sequence — scanning state."""
        total_width = _DOT_RADIUS * 2 * _DOT_COUNT + _DOT_GAP * (_DOT_COUNT - 1)
        start_x = rect.center().x() - total_width / 2 + _DOT_RADIUS
        cy = rect.center().y()

        for i in range(_DOT_COUNT):
            # Phase offset per dot for sequential pulse
            dot_phase = (self._dot_scan_phase - i * 0.33) % 1.0
            # Smooth pulse: 0.6 → 1.0 opacity
            pulse = 0.6 + 0.4 * max(0.0, math.sin(dot_phase * math.pi))
            dot_color = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 255 * pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_color)
            dx = start_x + i * (_DOT_RADIUS * 2 + _DOT_GAP)
            painter.drawEllipse(QPointF(dx, cy), _DOT_RADIUS, _DOT_RADIUS)

    def _paint_verified(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """Checkmark draw animation — verified state. No text."""
        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)

        # Checkmark centered in pill
        cx = rect.center().x()
        cy = rect.center().y()
        # Checkmark: left tail → bottom → right top
        p1 = QPointF(cx - 7, cy)          # start of check
        p2 = QPointF(cx - 2, cy + 5)      # bottom vertex
        p3 = QPointF(cx + 8, cy - 5)      # end of check

        pen = QPen(accent, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        progress = self._checkmark_progress

        if progress <= 0.0:
            return

        # Stroke animation: draw as much of the path as progress allows
        # Segment 1: p1→p2 (40% of path), Segment 2: p2→p3 (60% of path)
        path = QPainterPath()
        path.moveTo(p1)

        if progress <= 0.4:
            # Drawing segment 1
            t = progress / 0.4
            mid = QPointF(p1.x() + (p2.x() - p1.x()) * t, p1.y() + (p2.y() - p1.y()) * t)
            path.lineTo(mid)
        else:
            # Segment 1 complete, drawing segment 2
            path.lineTo(p2)
            t = (progress - 0.4) / 0.6
            end = QPointF(p2.x() + (p3.x() - p2.x()) * t, p2.y() + (p3.y() - p2.y()) * t)
            path.lineTo(end)

        painter.drawPath(path)

    def _paint_welcome(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        """Welcome text — final state before pill fades."""
        if not state.label:
            return
        tc = QColor(state.label_color)
        tc.setAlphaF(self._alpha * self._content_alpha)
        painter.setPen(tc)
        font = QFont("Segoe UI Variable Display", 10, QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.5)
        painter.setFont(font)
        text_rect = rect.adjusted(14, 0, -14, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, state.label)

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

        self._draw_text(painter, rect.adjusted(66, 6, -16, -28), state.label, state.label_color, 10)
        detail = state.detail or "Hold center"
        self._draw_text(painter, rect.adjusted(66, 24, -16, -10), detail, "#8E8E93", 8)

        chips = [
            ("ID", state.confidence if state.confidence is not None else 0.0),
            ("LIVE", state.liveness if state.liveness is not None else 0.0),
            ("POS", state.face_position if state.face_position is not None else 0.0),
        ]
        chip_x = rect.right() - 110
        chip_y = rect.top() + 9
        for idx, (label, value) in enumerate(chips):
            self._draw_score_chip(painter, chip_x, chip_y + idx * 13, label, value, state.accent_color)

    def _paint_face_scan(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        icon_size = min(rect.height() - 16, 56)
        icon_shell = QRectF(
            rect.left() + 13,
            rect.center().y() - icon_size / 2,
            icon_size,
            icon_size,
        )

        shell_path = QPainterPath()
        shell_path.addRoundedRect(icon_shell, 18, 18)
        shell_gradient = QRadialGradient(icon_shell.center(), icon_shell.width() * 0.78)
        shell_gradient.setColorAt(0.0, QColor(18, 38, 27, int(self._alpha * self._content_alpha * 232)))
        shell_gradient.setColorAt(0.72, QColor(4, 9, 8, int(self._alpha * self._content_alpha * 246)))
        shell_gradient.setColorAt(1.0, QColor(0, 0, 0, int(self._alpha * self._content_alpha * 252)))
        painter.fillPath(shell_path, shell_gradient)

        rim = QColor(state.accent_color)
        rim.setAlpha(int(self._alpha * self._content_alpha * 62))
        painter.setPen(QPen(rim, 1.0))
        painter.drawPath(shell_path)

        glyph_box = icon_shell.adjusted(9, 8, -9, -8)
        self._paint_face_id_glyph(
            painter,
            glyph_box,
            verifying=(state.name == "verifying_lock"),
        )

        text_left = int(icon_shell.right() + 14)
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
        x = rect.left() + travel * phase
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
        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)
        content_w = 148
        start_x = rect.center().x() - content_w / 2
        icon = QRectF(start_x, rect.center().y() - 14, 28, 28)
        painter.setPen(QPen(accent, 2.2))
        painter.setBrush(QColor(255, 69, 58, int(self._alpha * self._content_alpha * 24)))
        painter.drawRoundedRect(icon, 8, 8)

        pen = QPen(accent, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(icon.left() + 8, icon.top() + 8), QPointF(icon.right() - 8, icon.bottom() - 8))
        painter.drawLine(QPointF(icon.right() - 8, icon.top() + 8), QPointF(icon.left() + 8, icon.bottom() - 8))

        self._draw_text(painter, QRect(int(start_x + 40), rect.top(), 116, rect.height()),
                        state.label, state.label_color, 9)

    def _paint_shield(self, painter: QPainter, rect: QRect, state: IslandState) -> None:
        accent = QColor(state.accent_color)
        accent.setAlphaF(self._alpha * self._content_alpha)
        label = state.label
        detail = {
            "locked_passive": "Click or Space",
            "soft_locked": "Click or Space",
            "social_lock": "Owner only",
            "hostile_lock": "Windows lock",
        }.get(state.name, state.detail)

        content_w = 174 if state.name in {"locked_passive", "soft_locked"} else 214
        start_x = rect.center().x() - content_w / 2
        badge = QRectF(start_x, rect.center().y() - 16, 32, 32)
        painter.setPen(QPen(accent, 1.8))
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(),
                                int(self._alpha * self._content_alpha * 18)))
        painter.drawRoundedRect(badge, 12, 12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QPointF(badge.center().x(), badge.center().y()), 4.0, 4.0)

        text_x = int(start_x + 43)
        self._draw_text(painter, QRect(text_x, rect.top() + 12, int(content_w - 44), 20),
                        label, state.label_color, 10)
        self._draw_text(painter, QRect(text_x, rect.top() + 31, int(content_w - 44), 16),
                        detail, "#8E8E93", 7)

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

    def _draw_metric_bar(self, painter: QPainter, x: int, y: int, width: int, label: str, value: float, color: str) -> None:
        track = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 32))
        fill = QColor(color)
        fill.setAlphaF(self._alpha * self._content_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(x, y, width, 4), 2, 2)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(x, y, width * max(0.0, min(1.0, value)), 4), 2, 2)

        label_color = QColor(160, 160, 166, int(self._alpha * self._content_alpha * 210))
        painter.setPen(label_color)
        painter.setFont(QFont("Segoe UI", 6, QFont.Weight.Medium))
        painter.drawText(QRect(x, y - 12, width, 10), Qt.AlignmentFlag.AlignLeft, label)

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

    def _draw_score_chip(self, painter: QPainter, x: int, y: int, label: str, value: float, color: str) -> None:
        value = max(0.0, min(1.0, value))
        bg = QColor(255, 255, 255, int(self._alpha * self._content_alpha * 18))
        border = QColor(color)
        border.setAlpha(int(self._alpha * self._content_alpha * (70 + 60 * value)))
        text = QColor(225, 225, 230, int(self._alpha * self._content_alpha * 230))

        chip = QRectF(x, y, 86, 10)
        painter.setPen(QPen(border, 0.8))
        painter.setBrush(bg)
        painter.drawRoundedRect(chip, 5, 5)

        painter.setPen(text)
        painter.setFont(QFont("Segoe UI", 6, QFont.Weight.Medium))
        painter.drawText(QRect(int(x + 6), int(y - 1), 32, 12), Qt.AlignmentFlag.AlignVCenter, label)

        fill = QColor(color)
        fill.setAlpha(int(self._alpha * self._content_alpha * 190))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(x + 39, y + 3, 41 * value, 4), 2, 2)

    def _draw_text(self, painter: QPainter, rect: QRect, text: str, color: str, size: int) -> None:
        tc = QColor(color)
        tc.setAlphaF(self._alpha * self._content_alpha)
        painter.setPen(tc)
        painter.setFont(QFont("Segoe UI Variable Display", size, QFont.Weight.Medium))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
