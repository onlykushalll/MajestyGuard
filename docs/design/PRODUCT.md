# Product

## Register

product

## Users

Windows 11 power users who want continuous face-based screen protection. Context: at desk, laptop/webcam active, multi-monitor setups. Job: machine stays unlocked while owner present, locks instantly when they leave or stranger appears.

## Product Purpose

MajestyGuard is a face-lock daemon that monitors the webcam continuously and manages screen lock state based on facial recognition. It bridges the gap between Windows Hello (one-shot login) and continuous presence verification. Success = zero unauthorized access incidents with zero friction for the owner.

## Brand Personality

Vigilant, invisible, trustworthy. The UI should feel like a luxury security system — present when needed, gone when not. Think premium car dashboard warning lights: minimal surface area, maximum information density, instant recognition.

## Anti-references

- Cheap security software with loud red/yellow UIs and fear-based messaging
- Generic Windows system dialogs (gray, boxy, lifeless)
- Flashy consumer apps with excessive animation and decoration
- Gaming overlay aesthetics (neon, aggressive gradients)
- Antivirus UIs with progress bars and threat counters

## Design Principles

1. **Disappear when trusted** — The UI's best state is invisible. Owner verified = pill shrinks to nothing.
2. **Escalate with restraint** — Threat level maps to visual weight, never to noise. No popups, no sounds.
3. **Earn trust through precision** — Every pixel of the Dynamic Island communicates system state. No decoration.
4. **Respect the desktop** — The overlay is a guest on the user's screen. Minimal footprint, no disruption.
5. **Fail visibly, recover silently** — Errors get clear visual states. Recovery happens without fanfare.

## Accessibility & Inclusion

- High contrast mode support (WCAG AA minimum for all text on pill backgrounds)
- Reduced motion support via MG_UI_REDUCE_MOTION env var
- Color-blind safe: states distinguished by shape/size/position, not color alone
- Screen reader not applicable (overlay is visual-only security indicator)
