"""pipe_security.py — Dynamically builds user-bound SDDL configurations for MajestyGuard named pipes."""
import win32api
import win32security


def build_user_pipe_sddl() -> str:
    """
    Constructs an SDDL string granting Full Control to SYSTEM (SY)
    and Built-in Administrators (BA), and Read/Write access to the
    currently logged-in user's SID.
    """
    try:
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32security.TOKEN_QUERY
        )
        try:
            sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        finally:
            token.Close()
        user_sid_str = win32security.ConvertSidToStringSid(sid)
        return f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;{user_sid_str})"
    except Exception as exc:
        raise RuntimeError("Could not resolve the current user SID for local pipe access") from exc
