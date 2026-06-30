# MajestyGuard — Deep UI/UX Audit (using impeccable, taste-skill-v1, ui-ux-pro-max)

**Date:** 2026-06-19
**Auditor:** Z.ai Code (impeccable v3.7.1 + taste-skill-v1 + ui-ux-pro-max + visual-design-foundations + frontend-design)
**Files Fully Read:**
- `ui/island.py` (1124 lines — full)
- `ui/soft_lock.py` (845 lines — full)
- `ui/main.py` (234 lines — full)
- `ui/states.py` (253 lines — full)
- `src/MajestyGuard.Overlay/DynamicIslandWindow.xaml.cs` (748 lines — full)
- `src/MajestyGuard.Overlay/EnrollmentWindow.xaml.cs` (786 lines — full)
- `src/MajestyGuard.Overlay/LockScreenGuard.cs` (261 lines — full)
- `src/MajestyGuard.Overlay/DynamicIslandWindow.xaml` (574 lines — full)
- `src/MajestyGuard.Overlay/EnrollmentWindow.xaml` (482 lines — full)

---

## 📋 Summary

| Severity | Count |
|----------|-------|
| **Critical** | 5 |
| **High** | 10 |
| **Medium** | 14 |
| **Low** | 8 |
| **UI/UX Design** | 15 |
| **Total** | **52** |

---

## 🚨 CRITICAL (5)

### U1: Keyboard Hook Blocks ALL KeyUp Events — Stuck Keys
**File:** `ui/soft_lock.py:109-117`
**Severity:** CRITICAL

```python
def _keyboard_ll_callback(nCode, wParam, lParam):
    if nCode >= 0 and _overlay_locked:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            if kb.vkCode in (VK_TAB, VK_SPACE) and not _any_modifier_held():
                return ctypes.windll.user32.CallNextHookEx(_kb_hook_id, nCode, wParam, lParam)
            return 1
        return 1  # — BLOCKS ALL OTHER EVENTS INCLUDING KEYUP!
    return ctypes.windll.user32.CallNextHookEx(_kb_hook_id, nCode, wParam, lParam)
```

Line 116: `return 1` is reached for ALL non-keydown events (including WM_KEYUP, WM_SYSKEYUP). This blocks ALL key release events. Keys pressed before the lock engages stay "pressed" in the OS's internal state. When the lock releases, the still-pressed state causes auto-repeat storms.

**Fix:** Only block KEYDOWN/SYSKEYDOWN. Pass KEYUP/SYSKEYUP through:
```python
if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
    # ... block logic
    return 1
# KEYUP and all other events pass through
return ctypes.windll.user32.CallNextHookEx(_kb_hook_id, nCode, wParam, lParam)
```

---

### U2: Unblurred Desktop Screenshot Persists in Memory
**File:** `ui/soft_lock.py:378-395`
**Severity:** CRITICAL (Privacy)

`_capture_background()` calls `screen.grabWindow(0)` which captures the full unblurred desktop. This is stored as `shot_image` (QImage), then passed to a background thread for blur processing. During the ~100-300ms blur computation, the **unblurred screenshot** exists in memory as `shot_image`. A memory dump during this window exposes the user's screen contents.

**Fix:** Delete the unblurred image immediately after passing its data to the blur thread:
```python
def _worker():
    try:
        # ... blur computation ...
        blurred = second.scaled(...)
        # Immediately destroy the unblurred copy
        del shot_image
        # ... rest of processing using only blurred
```

---

### U3: Task Manager Force-Close Loop — Race Condition
**File:** `ui/soft_lock.py:577-583`
**Severity:** CRITICAL

```python
hwnd_taskmgr = user32.FindWindowW("TaskManagerWindow", None)
if hwnd_taskmgr:
    user32.PostMessageW(hwnd_taskmgr, WM_CLOSE, 0, 0)
```

This runs every 250ms (`_tick`). If Task Manager is already closing, `PostMessageW` may fail silently. If a new Task Manager spawns before the old one closes, the close message goes to the wrong window. More critically, this runs on the UI thread via QTimer, and `FindWindowW` is a blocking call that can stutter the animation.

**Fix:** Move to a separate thread, and use `WaitForSingleObject` to verify the window actually closed before scanning again.

---

### U4: SetWindowLongPtr Missing EntryPoint — Crash on x64
**File:** `src/MajestyGuard.Overlay/DynamicIslandWindow.xaml.cs:44-48`
**Severity:** CRITICAL

```csharp
[DllImport("user32.dll")]
private static extern nint SetWindowLongPtr(nint hWnd, int nIndex, nint dwNewLong);
```

On x64 Windows, `user32.dll` does not export `SetWindowLongPtr` — it exports `SetWindowLongPtrW`. The P/Invoke will throw `EntryPointNotFoundException` at runtime when the overlay first activates, crashing the overlay process.

**Fix:**
```csharp
[DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
private static extern nint SetWindowLongPtr(nint hWnd, int nIndex, nint dwNewLong);

[DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
private static extern nint GetWindowLongPtr(nint hWnd, int nIndex);
```

---

### U5: Enrollment Captures JPEGs to Disk — Privacy Risk
**File:** `src/MajestyGuard.Overlay/EnrollmentWindow.xaml.cs:261-294`
**Severity:** CRITICAL (Privacy)

`SendEnrollCaptureAsync` writes face capture JPEGs to `%LOCALAPPDATA%\MajestyGuard\enrollment\Front.jpg` etc. These files are **never deleted** after enrollment. Any process can read the user's face photos from disk.

**Fix:** Delete the JPEGs after embeddings are extracted:
```csharp
// After FinalizeEnrollmentAsync succeeds:
foreach (var path in _capturedFramePaths.Values)
{
    try { File.Delete(path); } catch { }
}
_capturedFramePaths.Clear();
```

---

## ⚠️ HIGH (10)

### U6: No Accessibility Support — Screen Reader Blind
**Files:** ALL UI files
**Severity:** HIGH

Neither the PyQt6 UI nor the WinUI 3 overlay implements any accessibility features:
- No `AutomationProperties.Name` on any control (XAML)
- No `QAccessible` descriptions (PyQt6)
- No keyboard focus indicators
- No high-contrast mode support
- No screen reader announcements for state changes

A visually impaired user cannot use MajestyGuard. For a security product, this means a disabled user is locked out of their own PC.

### U7: No Reduced-Motion Check (C# Overlay)
**File:** `src/MajestyGuard.Overlay/DynamicIslandWindow.xaml.cs`
**Severity:** HIGH

The Python island widget checks `MG_UI_REDUCE_MOTION` env var (island.py:58), but the C# overlay does NOT check `SystemParameters.ClientAreaAnimation` or `UISettings.AnimationsEnabled`. All WinUI animations play regardless of the user's accessibility settings.

### U8: Camera Preview Stretched — Aspect Ratio Not Maintained
**File:** `src/MajestyGuard.Overlay/EnrollmentWindow.xaml` (PreviewImage control)
**Severity:** HIGH

The `PreviewImage` and `CapturePreview` Image controls don't set `Stretch="UniformToFill"`. The camera frame is stretched to fill the container, distorting the face. This makes it hard for users to position their face correctly in the enrollment oval.

### U9: No Clock on Lock Screen — User Disorientation
**File:** `ui/soft_lock.py` (paintEvent)
**Severity:** HIGH

The soft lock overlay shows a blurred desktop with a status pill ("LOCKED", "VERIFYING") and a brand signature ("Secured by MajestyGuard"), but **no clock**. Users who step away and return can't tell what time it is, which is disorienting. Windows' own lock screen shows a large clock — MajestyGuard's doesn't.

### U10: No Error Recovery During Enrollment
**File:** `src/MajestyGuard.Overlay/EnrollmentWindow.xaml.cs:311-448`
**Severity:** HIGH

`FinalizeEnrollmentAsync` calls a Python subprocess to extract embeddings. If the subprocess crashes, the error message is technical: `"Face processing failed: Python exited 1"`. There's no retry button — the user must close the wizard and restart from step 1.

### U11: Fallback Button Only Appears After 20 Seconds
**File:** `ui/soft_lock.py:585-602`
**Severity:** HIGH

The "Press TAB - Windows lock" fallback button is hidden for the first 20 seconds of lock. If the camera is broken or face recognition fails, the user is trapped for 20 seconds with no escape. This is a UX emergency.

### U12: PipeReader Busy-Waits at 60Hz
**File:** `ui/main.py:97`
**Severity:** HIGH (Performance)

```python
time.sleep(0.016)  # 60 Hz polling
```

The pipe reader polls the named pipe 60 times per second, even when no state change is happening. This wastes CPU on every frame. Named pipes support blocking reads — the reader should block on `ReadFile` instead of polling.

### U13: Enrollment Step Indicator Inconsistent
**File:** `src/MajestyGuard.Overlay/EnrollmentWindow.xaml.cs:100-116`
**Severity:** HIGH

The `Angles` array has 4 entries (Front, SlightLeft, SlightRight, WithGlasses), but the `Steps` collection has 6 entries (Welcome, Camera check, Face front, Turn left, Turn right, With glasses). The step numbers are 1-6, but the angle indices are 0-3. If a user skips the glasses step, step 6 ("With glasses") is marked complete but no capture happened. The `_capturedFramePaths` check (`Count < 2`) is the only guard.

### U14: Dynamic Island Overlaps Windows Notifications
**File:** `ui/island.py:457-465`
**Severity:** HIGH

The island widget is positioned at `y = screen.y() + 8` (top-center). Windows notification toasts also appear at top-center. The island covers notifications, making them invisible.

### U15: State Change Animation Duration Too Short
**File:** `ui/island.py:397-408`
**Severity:** HIGH

Content fade uses `step = 0.16` per frame at 60fps = ~100ms total. This is too fast for users to perceive the transition. Apple's Dynamic Island uses 250-300ms for content changes. The animation feels jarring and mechanical.

---

## 📋 MEDIUM (14)

| # | File | Issue |
|---|------|-------|
| U16 | `island.py:105` | Fixed window size 500x120 — on multi-monitor setups with different DPIs, the pill may be clipped |
| U17 | `island.py:467-481` | `setMask` called every frame during morph — expensive, should only update when size changes significantly |
| U18 | `soft_lock.py:138-159` | Hook thread uses `PeekMessageW` + `sleep(0.01)` — should use `GetMessageW` (blocking) for zero-CPU idle |
| U19 | `soft_lock.py:508-520` | Noise texture generated with integer hash — has visible banding on high-DPI displays |
| U20 | `island.py:362-372` | Flash animation uses hard `0.22` alpha dip — too aggressive, looks like a glitch rather than a flash |
| U21 | `states.py:96-103` | `locked` state has `height=12` — too small for any content, renders as a thin line |
| U22 | `EnrollmentWindow.xaml.cs:180` | Camera switch cycles 0-3, but doesn't check if cameras at indices 2-3 exist |
| U23 | `DynamicIslandWindow.xaml.cs:604-666` | `CaptureAndBlurDesktop` creates a new `GraphicsCaptureSession` every time the overlay state changes — should cache |
| U24 | `DynamicIslandWindow.xaml.cs:672-685` | `ScheduleMemoryTrim` disposes `_desktopSnapshot` after 5s, but if the user returns to lock state before that, the snapshot is null and blur doesn't render |
| U25 | `soft_lock.py:716-720` | Corner status pill is hardcoded 176x34px — doesn't scale with DPI |
| U26 | `island.py:412-417` | Spring physics for morph use `stiffness=0.18, damping=0.70` — overdamped, feels sluggish. Apple uses underdamped springs for snappier feel |
| U27 | `EnrollmentWindow.xaml.cs:672-677` | `OnFrameArrived` creates `SoftwareBitmap.Copy` every frame — 15 FPS of full-frame copies causes GC pressure |
| U28 | `soft_lock.py:343` | `_set_taskbar_visible(False)` is called but the function is a no-op (line 210-211: `return`) — taskbar is never actually hidden |
| U29 | `island.py:149-150` | Scanning state from idle is silently ignored — if the daemon sends "scanning" while UI is idle, the pill doesn't appear |

---

## 📋 LOW (8)

| # | File | Issue |
|---|------|-------|
| U30 | `island.py:22-28` | Magic numbers for timing constants — should be configurable |
| U31 | `island.py:105` | `setFixedSize(500, 120)` — hardcoded, doesn't adapt to screen size |
| U32 | `soft_lock.py:240-258` | Fallback button stylesheet is duplicated (lines 241-255 and 347-361) — DRY violation |
| U33 | `states.py:42-47` | `idle` state has empty label `""` — the pill renders as a blank black rectangle |
| U34 | `main.py:226-228` | `timer.timeout.connect(lambda: None)` — dummy timer to let Ctrl+C propagate, but it wakes the event loop 2x/second for no reason |
| U35 | `island.py:583-593` | `apply_state` has 7 nested if/elif branches for state name — label mapping — should use a dict lookup |
| U36 | `DynamicIslandWindow.xaml.cs:133-135` | Idle suppress timer fires every 45s — `SetThreadExecutionState` only needs to be called once with `ES_CONTINUOUS` |
| U37 | `EnrollmentWindow.xaml.cs:103` | `Angles` array subtitle has mojibake: `"15°"` — encoding issue from copy-paste |

---

## 📋 UI/UX DESIGN REFINEMENTS (15)

### Visual Design (using impeccable + taste-skill-v1)

1. **Color palette is generic dark.** All states use near-black backgrounds (`#030303`, `#020406`, `#050202`). The only differentiation is border/accent colors. For a security product, consider a unique brand identity — deep teal (`oklch(0.25 0.05 200)`) or royal indigo (`oklch(0.20 0.08 280)`) instead of generic black.

2. **Typography lacks hierarchy.** The island pill uses `QFont("Segoe UI Variable Text", 9, Medium)` for everything. State labels, confidence values, and detail text all have the same weight. Use `SemiBold` for state names, `Regular` for detail text.

3. **No micro-interactions on the island pill.** The pill responds to clicks (verification request), but there's no visual hover state. Users don't know it's clickable. Add a subtle glow or scale on hover.

4. **Spacing is inconsistent.** The island uses `_PAD = 8`, the soft lock uses `margin = max(24, min(42, rect.width() // 48))`, and the enrollment window uses `680-860px` width. Standardize on an 8px grid: 8, 16, 24, 32, 48.

5. **No empty states.** When no face is detected during verification, the pill just shows "Verifying" with a scan animation. Add feedback: "Looking for face..." with a subtle pulse.

6. **Lock screen has no clock.** As noted in U9, the lock screen is a blurred desktop with pills. Add a large clock display (like Apple/Windows lock screens):
   ```python
   def _paint_clock(self, painter):
       now = datetime.now()
       time_str = now.strftime("%H:%M")
       font = QFont("Segoe UI Variable Display", 72, QFont.Weight.Light)
       painter.setFont(font)
       painter.setPen(QColor(255, 255, 255, 200))
       painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, time_str)
   ```

7. **No loading states during enrollment finalization.** `FinalizeEnrollmentAsync` takes 2-5 seconds. The only feedback is `"Generating face profile — please wait..."` text. Add a progress ring or animated dots.

8. **Dynamic Island animation easing is mechanical.** The morph uses spring physics (`stiffness=0.18, damping=0.70`), but this is overdamped — it slowly settles without any bounce. Apple's Dynamic Island uses underdamped springs (slight overshoot). Try `stiffness=0.25, damping=0.55` for a snappier feel.

9. **No theme support.** Always dark mode. The soft lock uses light glass (`QColor(246, 248, 252, 119)`), but the island is dark. The two don't feel like the same product. Choose one aesthetic.

10. **Camera preview has no alignment guide.** The enrollment oval is shown in XAML, but there's no face detection overlay showing whether the face is centered/detected. Add a green checkmark when the face is in position.

### UX Flow (using ui-ux-pro-max)

11. **Enrollment flow is confusing.** 6 steps, but only 4 capture angles. Steps 1-2 are setup (welcome, camera check), steps 3-6 are captures. No progress bar — just a step indicator. Add "Step 3 of 6" text.

12. **No feedback during detection.** When the user sits down after being away, the pill shows "Locked" then "Verifying" — but there's no "Detecting face..." intermediate state. The user doesn't know if the camera is working.

13. **Error messages are technical.** "Face processing failed: Python exited 1" — users don't understand this. Use friendly messages: "Could not process your face data. Check your lighting and try again."

14. **No escape hatch if biometric fails permanently.** If the camera breaks, the user is locked out. The fallback button (TAB — Windows lock) appears after 20s, but there's no way to disable MajestyGuard from the lock screen. Add an emergency PIN override.

15. **No reduced-motion path for the soft lock.** The blurred glass overlay with noise texture and pulsing glows is visually heavy. For users with motion sensitivity, provide a flat solid-color overlay option.

---

## 📋 PRIORITY FIX ORDER

### Immediate (UI is broken without these)
1. **U1** — Keyboard KEYUP blocked (stuck keys)
2. **U4** — SetWindowLongPtr crash on x64
3. **U5** — Enrollment JPEGs left on disk
4. **U2** — Unblurred screenshot in memory
5. **U3** — Task Manager force-close race

### High Priority (UX critical)
6. **U11** — Fallback button hidden for 20s
7. **U9** — No clock on lock screen
8. **U6** — No accessibility
9. **U10** — No error recovery during enrollment
10. **U12** — 60Hz pipe polling wastes CPU

### Medium Priority
11-24: Handle per-file as needed

### Low Priority
25-32: Cleanup during refactoring

### Design Refinements
33-47: Apply during polish pass

---

*Generated by Z.ai Code with impeccable v3.7.1 + taste-skill-v1 + ui-ux-pro-max + visual-design-foundations + frontend-design*
*Total UI issues: 52 | Files audited: ~5,000 lines of UI code*
