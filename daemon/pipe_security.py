"""pipe_security.py — Dynamically builds user-bound SDDL configurations for MajestyGuard named pipes."""
import logging

import win32api
import win32security

log = logging.getLogger(__name__)


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
        sid, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
        user_sid_str = win32security.ConvertSidToStringSid(sid)
        return f"D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;{user_sid_str})"
    except Exception:
        # Fallback to Interactive User (IU) if query fails. This is a real
        # widening of who can open the pipe: IU covers every
        # interactively-logged-on user on the machine, not just this one,
        # so it must never fail silently. On a single-user machine the
        # practical risk is low, but if this ever fires it means the
        # intended tight, user-specific ACL did not get applied, and that
        # is worth knowing about rather than discovering by accident.
        log.error(
            "Could not resolve this process's own SID for the command/IPC "
            "pipe ACL; falling back to the broader Interactive-User (IU) "
            "group instead of this specific user. Investigate if this "
            "recurs.",
            exc_info=True,
        )
        return "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GWGR;;;IU)"
