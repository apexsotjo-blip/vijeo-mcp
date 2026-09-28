"""Diagnose Vijeo Designer's COM automation on this PC.

Vijeo-Frame.exe registers automation classes (VijeoFrame.VijeoDesigner2, VJDProject2,
VJDTarget2, VJDPanel2: OpenProject, Build, GetCrossReferences, panel Export/Import,
Bind...). Whether they are callable depends on the installation's registration:
the class behind the ProgID must point to a registered type library whose
interfaces the running server really implements.

Verified on Vijeo Designer 6.2 (this project's reference PC): the ProgID class's
type library {0B02AAD0-...} is not registered and not shipped, and Vijeo-Frame.tlb
belongs to a different build (its interfaces are not implemented by the server) ->
automation unusable. A Vijeo repair/reinstall may fix it; run this diagnostic after.
"""

from __future__ import annotations

import subprocess
import time
import winreg

PROGID = "VijeoFrame.VijeoDesigner2"


def _read(root, path, value=""):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def _typelib_registered(libid: str) -> list[str]:
    found = []
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{libid}", 0, winreg.KEY_READ | view) as k:
                i = 0
                while True:
                    try:
                        ver = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    path = _read(winreg.HKEY_CLASSES_ROOT, rf"TypeLib\{libid}\{ver}\0\win32")
                    found.append(f"{ver} -> {path}")
                    i += 1
        except OSError:
            pass
    return sorted(set(found))


def _vijeo_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Vijeo-Frame.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {int(l.split('","')[1]) for l in out.splitlines() if l.startswith('"Vijeo-Frame')}


def diagnose(live: bool = False) -> dict:
    clsid = _read(winreg.HKEY_CLASSES_ROOT, rf"{PROGID}\CLSID")
    rep = {"progid": PROGID, "clsid": clsid}
    if not clsid:
        rep["verdict"] = "Vijeo Designer automation is not registered on this PC."
        return rep
    rep["server"] = _read(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32")
    libid = _read(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\TypeLib")
    rep["class_typelib"] = libid
    rep["typelib_registrations"] = _typelib_registered(libid) if libid else []
    ok_static = bool(rep["server"]) and bool(rep["typelib_registrations"])
    rep["static_check"] = "ok" if ok_static else "type library of the automation class is not registered"
    if live:
        rep["live"] = _live_probe()
    usable = ok_static and (not live or rep["live"].get("calls_work"))
    rep["verdict"] = ("Automation looks usable" + (" (live-tested)." if live else "; run with live=True to confirm.")
                      if usable else
                      "Automation is NOT usable on this installation (registration mismatch). "
                      "Use backup analysis + import files; try a Vijeo Designer repair, then re-run this diagnostic.")
    return rep


def _live_probe() -> dict:
    """Start OUR OWN Vijeo instance, call one harmless method, close only that instance."""
    import pythoncom
    import win32com.client

    before = _vijeo_pids()
    res: dict = {}
    pythoncom.CoInitialize()
    try:
        t = time.time()
        app = win32com.client.DispatchEx(PROGID)
        res["created_s"] = round(time.time() - t, 1)
        try:
            res["IsAvailable"] = app.IsAvailable()
            res["calls_work"] = True
        except Exception as e:  # noqa: BLE001
            res["calls_work"] = False
            res["error"] = str(e)
        del app
    except Exception as e:  # noqa: BLE001
        res["calls_work"] = False
        res["error"] = str(e)
    finally:
        time.sleep(2)
        mine = _vijeo_pids() - before
        for p in mine:
            subprocess.run(["taskkill", "/PID", str(p), "/F"], capture_output=True)
        res["own_instances_closed"] = sorted(mine)
        pythoncom.CoUninitialize()
    return res
