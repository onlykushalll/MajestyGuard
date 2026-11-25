# Product

## Register

product

## Users

MajestyGuard is built for a local Windows 11 owner who wants the laptop to stay useful while becoming resistant to casual walk-up use. The primary user is usually alone, returning to the device after short absences, and needs fast owner verification without stopping background work.

## Product Purpose

MajestyGuard provides a local-first presence security layer with face recognition, passive liveness checks, a fullscreen soft-lock overlay, and a Dynamic Island-style status surface. Success means the owner can resume quickly, strangers and spoof attempts cannot unlock the desktop, and hostile situations fall back to Windows lock instead of pretending the soft lock is Secure Desktop.

## Standing Workflow Decisions

- Windows sign-in is sufficient at logon. Installing the app must not enable startup or start a camera scan.
- Input idle triggers an owner check, not an immediate workstation lock. Keep model prewarming quiet and the camera parked until the check is due. A routine match confirms quietly, releases the camera and schedules another presence check; reserve the welcome animation for a return from the overlay.
- Watching or reading without input is not evidence of sleep. Any future sustained-eyes-closed feature must be opt-in and calibrated; a blink, poor light, or obscured eyes must not count as sleep.
- The overlay protects the current desktop session while background work continues. Space requests one bounded verification attempt; repeated presses must not create additional workers. Keep the Windows recovery route and never claim Secure Desktop guarantees.
- Face setup fills directional coverage from distinct, acceptable live samples. Do not replace the existing profile on cancel, failure, or upgrade. Replacing an unrelated profile is a separate, account-authorized action.
- Do not ask for routine re-enrollment to compensate for lighting, weaken liveness to meet a latency goal, or claim a timing improvement without measuring it on the device.
- Physical replay testing is excluded by the owner, not passed. Privileged sign-in integration is outside this release.

## Brand Personality

Serious, calm, precise. The interface should feel like a trusted security appliance embedded into Windows: quiet when everything is normal, unmistakable when intervention is needed, and never theatrical at the cost of clarity.

## Anti-references

Avoid generic dark dashboards, over-decorated glass effects, noisy neon security tropes, emoji/icon gimmicks, and any UI that hides recovery actions or leaves the user unsure whether the camera is working. Do not present the user-space soft lock as equivalent to the Windows Secure Desktop.

## Design Principles

1. Security decisions remain explicit and local; UI never grants access by itself.
2. The owner path must be fast, readable, and forgiving under real lighting and camera conditions.
3. Recovery paths must be visible enough that a camera failure never feels like a trap.
4. Motion should communicate state transitions, not decorate the surface.
5. Production claims require fresh verification, especially around biometric and IPC boundaries.

## Accessibility & Inclusion

Target WCAG AA contrast for visible UI, preserve keyboard operation for recovery paths, support reduced-motion behavior, and avoid relying on color alone for security state. High-contrast Windows users and users with motion sensitivity should still be able to understand lock, verifying, verified, and fallback states.
