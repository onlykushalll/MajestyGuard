# Design

## Visual Theme

Dark-material, Apple Dynamic Island-inspired. Near-black pill floating at screen top-center. States communicate through size morphing, accent color shifts, and icon rendering — not chrome or decoration. Overlay uses frosted-glass desktop capture with subtle atmospheric gradients.

## Color Palette

### Core Materials
- `#030303` — pill body (near-black, most states)
- `#0A0A0A` — pill body (scanning)
- `#111111` — pill body (idle/exit)
- `#020406` — pill body (lock states, blue-tinted black)
- `#050202` — pill body (hostile/failure, red-tinted black)

### Accent Colors (State-Driven)
- `#34C759` — green: active/verified/verifying (Apple system green)
- `#FFB340` — amber: scanning/locked_passive/social_lock (warm warning)
- `#64D2FF` — cyan: soft_locked/enrolling/calibrating (neutral lock)
- `#FF453A` — red: stranger/hostile_lock/verify_failed (Apple system red)
- `#343438` — dark gray: idle/exit (dormant)
- `#663333` — muted red: locked (Windows lock handoff)

### Label Colors
- `#FFFFFF` — welcome text
- `#B9F6C8` / `#E7FFEC` — green-tinted whites (active/scanning/verifying)
- `#EAF7FF` / `#E8F7FF` — blue-tinted whites (soft_locked/enrolling)
- `#FFE3B0` — amber-tinted white (locked_passive/social_lock)
- `#FFD0CC` — red-tinted white (stranger/hostile/verify_failed)
- `#77777C` — muted gray (idle/exit)
- `#8E8E93` — iOS secondary gray (detail text)

### Overlay
- `rgb(246, 248, 252)` at alpha 119 — base coat
- `rgb(205, 235, 255)` — top-right radial glow (soft blue)
- `rgb(255, 226, 238)` — bottom-right radial glow (soft pink)
- `rgb(222, 233, 255)` — bottom-left radial glow (periwinkle)
- `rgb(44, 52, 64)` — bottom vignette

## Typography

| Usage | Family | Size | Weight |
|-------|--------|------|--------|
| Pill labels | Segoe UI Variable Display | 10px | Medium (500) |
| Welcome label | Segoe UI Variable Display | 10px | DemiBold (600) |
| Detail/subtitle | Segoe UI Variable Display | 7-8px | Medium |
| Score chips | Segoe UI | 6px | Medium |
| Overlay corner pill | Segoe UI Variable Text | 9px | Medium |

No font fallbacks declared. Windows-only target (Segoe UI Variable ships with Windows 11).

## Layout

- Canvas: fixed 500x120px (prevents DWM compositor jitter)
- Pill: centered horizontally, 8px from screen top
- Corner radius: height/2 (full pill radius)
- States define their own width/height (ranging from 118x12 idle to 326x70 hostile_lock)
- Multi-monitor: overlay uses virtualGeometry() for full coverage

## Animation

- Spring physics: stiffness=0.18, damping=0.70, unified 60fps QTimer
- Morph settle: 0.4px position threshold, 0.25px/frame velocity threshold
- Content crossfade: 80ms fade-out, swap state, 80ms fade-in
- Pulse: 1800ms period, sine wave on glow alpha
- Flash: 4 blinks at 110ms interval
- Scan sweep: 900ms period
- Checkmark draw: 150ms stroke animation
- Welcome dwell: 2000ms before fade
- Pill fade: 400ms linear opacity
- Overlay dissolve: 600ms OutCubic via QPropertyAnimation
- Reduced motion: MG_UI_REDUCE_MOTION=1 skips all animations

## Components

### IslandWidget (pill)
Modes: pill, dot_scan, verified, welcome, face_scan, enrollment, diagnostic, shield, failure, success

### SoftLockOverlay (fullscreen)
Frosted-glass background capture (CPU 4-pass downsample blur), atmospheric gradients, noise texture (192x192 tiled at 11% opacity), corner status pill, brand signature pill.

## Known Issues

- Flash alpha bottoms at 0.22 instead of 0.0 (blink never fully disappears)
- No font fallback chain for non-Windows environments
- Two animation systems (manual spring vs QPropertyAnimation) without unified timing
- Overlay _tick phase advances for unused paint method
