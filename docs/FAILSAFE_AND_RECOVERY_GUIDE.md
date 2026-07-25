# MajestyGuard — Failsafe & Emergency Recovery Architecture

This document details MajestyGuard's **Emergency Recovery Channel** and **Administrator Failsafe Mechanisms** designed to ensure the owner can never be permanently locked out of their Windows PC.

---

## 1. The Administrator Emergency Escape Channel (Case A)

By architectural design, MajestyGuard leaves **Task Manager (`Ctrl+Shift+Esc`) / Elevated Admin Process Execution** as an intentional **Administrator Failsafe**:

* **Why it exists:** If a hardware error occurs (e.g. camera USB cord unplugged, webcam driver freeze, or corrupted model file), biometric verification cannot complete. Rather than leaving the user stranded or forcing a hard hard-reboot, Windows **User Interface Privilege Isolation (UIPI)** ensures an elevated Task Manager (`Taskmgr.exe`) can gain focus and terminate the overlay.
* **How it recovers:** When the MajestyGuard daemon or overlay process is terminated via Task Manager, the built-in `_emergency_unlock` cleanup handler immediately executes.

---

## 2. Emergency Cleanup Handler (`_emergency_unlock`)

Location: `daemon/main.py`

When the process receives a termination signal (`SIGTERM`, `SIGINT`, or Task Manager end-task):
1. **State Reset:** Writes `UNLOCKED` to `%LOCALAPPDATA%\MajestyGuard\lock_state.txt`.
2. **Hook Release:** Automatically releases `WH_KEYBOARD_LL` and `WH_MOUSE_LL` low-level Win32 hooks.
3. **Cursor Release:** Calls `ClipCursor(NULL)` to restore mouse movement.
4. **PID Cleanup:** Removes `%LOCALAPPDATA%\MajestyGuard\daemon.pid` so `mg_monitor.py` does not attempt to loop-restart a broken daemon instance.

---

## 3. On-Screen Fallback Controls

In addition to Task Manager:
* **`Tab` Key (Windows Lock Handoff):** Pressing `Tab` while soft-locked immediately hands off security to native Windows Lock (`LockWorkStation()`), allowing Windows PIN, Password, or Windows Hello login.
* **`Space` Key (Manual Scan Request):** Triggers an on-demand 5-second camera face scan window.

---

## 4. Summary of Fail-Safe Protections

| Situation | Failsafe Action | Result |
| :--- | :--- | :--- |
| **Camera Physical Unplug / Hardware Failure** | Triggers straight `LockWorkStation` or user presses `Tab` | PC secured via Windows PIN/Password |
| **Daemon Crash or Freeze** | `_emergency_unlock` atexit handler writes `UNLOCKED` | Hooks & cursor unclipped instantly |
| **Emergency Recovery Need** | Press `Ctrl+Shift+Esc` (Task Manager) | Admin escape hatch recovers session |
