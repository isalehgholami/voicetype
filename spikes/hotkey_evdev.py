#!/usr/bin/env python3
"""T-0.5 — نمونه‌ی هات‌کی evdev (بدون grab): Super+Alt+V با press و hold.

نیاز: عضویت در گروه input و `pip install evdev`.
"""
import selectors

import evdev
from evdev import ecodes

COMBO = {ecodes.KEY_LEFTMETA, ecodes.KEY_LEFTALT, ecodes.KEY_V}


def main():
    devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
    kbds = [d for d in devs if ecodes.EV_KEY in d.capabilities() and ecodes.KEY_V in d.capabilities()[ecodes.EV_KEY]]
    sel = selectors.DefaultSelector()
    for d in kbds:
        sel.register(d, selectors.EVENT_READ)
    held: set[int] = set()
    active = False
    print("منتظر Super+Alt+V ... (Ctrl+C برای خروج)")
    while True:
        for key, _ in sel.select():
            for ev in key.fileobj.read():
                if ev.type != ecodes.EV_KEY or ev.value == 2:
                    continue
                (held.add if ev.value == 1 else held.discard)(ev.code)
                now = COMBO <= held
                if now and not active:
                    print("PRESS")
                elif not now and active:
                    print("RELEASE")
                active = now


if __name__ == "__main__":
    main()
