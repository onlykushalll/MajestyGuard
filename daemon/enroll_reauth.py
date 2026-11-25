"""Ask the person at the keyboard to prove they own this Windows account.

Used before the one action that can hand the PC to a different face: starting the face
profile over. Wraps the standard Windows credential dialog and checks what was typed with
LogonUser; nothing typed is stored, logged or sent anywhere, and the buffers are wiped.

The Windows pieces are injected so the decision logic is testable without Windows.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

# (username, domain, password) typed into the dialog, or None if the user cancelled.
Credentials = tuple[str, str, str]
Prompt = Callable[[], Optional[Credentials]]
Logon = Callable[[str, str, str], bool]


def authenticate(prompt: Prompt, logon: Logon) -> bool:
    """True only when the dialog was completed AND Windows accepts the credentials."""
    creds = prompt()
    if creds is None:
        return False
    user, domain, password = creds
    try:
        if not user or not password:
            return False
        return bool(logon(user, domain, password))
    except Exception:
        return False
    finally:
        creds = None  # noqa: F841  (drop our references promptly)
        password = ""
        user = domain = ""


def verify_windows_password(parent_hwnd: int = 0) -> bool:
    """Show the Windows 'Make sure it's you' dialog. Always False off Windows or on any error."""
    if sys.platform != "win32":
        return False
    try:
        return authenticate(lambda: _prompt(parent_hwnd), _logon)
    except Exception:
        return False


# --------------------------------------------------------------------------- Windows glue
def _prompt(parent_hwnd: int) -> Optional[Credentials]:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    credui = ctypes.WinDLL("credui", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")

    class CREDUI_INFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hwndParent", wintypes.HWND),
            ("pszMessageText", wintypes.LPCWSTR),
            ("pszCaptionText", wintypes.LPCWSTR),
            ("hbmBanner", wintypes.HBITMAP),
        ]

    info = CREDUI_INFOW(ctypes.sizeof(CREDUI_INFOW), parent_hwnd or None,
                        "Starting your face profile over replaces who can unlock this PC. "
                        "Enter your Windows password to continue.",
                        "MajestyGuard", None)
    auth_package = wintypes.ULONG(0)
    out_buf = ctypes.c_void_p()
    out_size = wintypes.ULONG(0)
    save = wintypes.BOOL(False)
    CREDUIWIN_ENUMERATE_CURRENT_USER = 0x200
    rc = credui.CredUIPromptForWindowsCredentialsW(
        ctypes.byref(info), 0, ctypes.byref(auth_package), None, 0,
        ctypes.byref(out_buf), ctypes.byref(out_size), ctypes.byref(save),
        CREDUIWIN_ENUMERATE_CURRENT_USER,
    )
    if rc != 0:                                   # 1223 = cancelled, anything else = failure
        return None
    try:
        user_len, dom_len, pw_len = wintypes.DWORD(256), wintypes.DWORD(256), wintypes.DWORD(256)
        user = ctypes.create_unicode_buffer(256)
        domain = ctypes.create_unicode_buffer(256)
        password = ctypes.create_unicode_buffer(256)
        ok = credui.CredUnPackAuthenticationBufferW(
            0, out_buf, out_size, user, ctypes.byref(user_len),
            domain, ctypes.byref(dom_len), password, ctypes.byref(pw_len),
        )
        if not ok:
            return None
        result = (user.value, domain.value, password.value)
        ctypes.memset(password, 0, ctypes.sizeof(password))
        return result
    finally:
        ctypes.memset(out_buf, 0, out_size.value)
        ole32.CoTaskMemFree(out_buf)


def _logon(user: str, domain: str, password: str) -> bool:  # pragma: no cover - needs Windows
    import win32api
    import win32con
    import win32security as security
    if not domain and "\\" in user:                # 'PC\\name' as returned by the dialog
        domain, user = user.split("\\", 1)
    token = security.LogonUser(user, domain or None, password, win32con.LOGON32_LOGON_INTERACTIVE,
                               win32con.LOGON32_PROVIDER_DEFAULT)
    try:
        current = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            def sid(handle):
                return security.ConvertSidToStringSid(security.GetTokenInformation(handle, security.TokenUser)[0])
            return sid(token) == sid(current)
        finally:
            current.Close()
    finally:
        token.Close()
