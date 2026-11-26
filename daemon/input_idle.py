"""
Windows last-input timer for MajestyGuard desktop soft-lock.

This measures keyboard/mouse inactivity only. It does not pause or restrict
background work; it simply lets the daemon decide when to raise the input shield.
"""
if __package__:
    from .idle_monitor import get_idle_seconds
else:
    from idle_monitor import get_idle_seconds

__all__ = ["get_idle_seconds"]
