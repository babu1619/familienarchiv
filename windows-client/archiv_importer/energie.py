"""Nachtmodus: verhindert unter Windows den Ruhezustand, solange gearbeitet wird."""

from __future__ import annotations

import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_aktiv = False


def wach_halten(an: bool) -> None:
    global _aktiv
    if an == _aktiv or sys.platform != "win32":
        _aktiv = an
        return
    import ctypes
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if an else ES_CONTINUOUS
    ctypes.windll.kernel32.SetThreadExecutionState(flags)
    _aktiv = an


def am_netzteil() -> bool | None:
    """True/False unter Windows, None wenn unbekannt."""
    if sys.platform != "win32":
        return None
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    s = Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    return s.ACLineStatus == 1
