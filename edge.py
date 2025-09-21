# edge.py — Live tuner (RAW | EDGES) + edge-triggered double Enter + idle single Enter
# Trigger: if CURRENT frame has any edges (mean(edges>0) > 0.0)
# Action:  press Enter, wait 2.0s, press Enter again
# Idle Action: if no trigger for 25s, press Enter once.
# Logs each action to the console.

import argparse, os, time, ctypes
from datetime import datetime

import numpy as np
import cv2
import mss
import pyautogui

# ---------- Simple timestamped logger ----------
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]  # HH:MM:SS.mmm
    print(f"[{ts}] {msg}")

# ---------- Enter sender (multimethod) ----------
user32 = ctypes.windll.user32
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SC_ENTER = 0x1C  # main Enter scancode

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("union", INPUT_UNION)]

def sendinput_enter_once():
    down = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(ki=KEYBDINPUT(0, SC_ENTER, KEYEVENTF_SCANCODE, 0, None)),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        union=INPUT_UNION(
            ki=KEYBDINPUT(0, SC_ENTER, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0, None)
        ),
    )
    user32.SendInput(2, ctypes.byref((INPUT * 2)(down, up)), ctypes.sizeof(INPUT))

def press_enter_multimethod_once() -> bool:
    ok = False
    try:
        pyautogui.press("enter"); ok = True
    except Exception:
        pass
    try:
        import keyboard  # optional; may require admin on some PCs
        keyboard.send("enter"); ok = True
    except Exception:
        pass
    try:
        sendinput_enter_once(); ok = True
    except Exception:
        pass
    return ok

def press_enter_double_multimethod_logged() -> bool:
    log("ACTION: ENTER (1/2)")
    e1 = press_enter_multimethod_once()
    log(f"RESULT: enter1={e1}")
    log("ACTION: WAIT 2000 ms")
    time.sleep(2.0)
    log("ACTION: ENTER (2/2)")
    e2 = press_enter_multimethod_once()
    log(f"RESULT: enter2={e2}")
    return e1 and e2

def press_enter_single_multimethod_logged() -> bool:
    log("ACTION: ENTER (idle timeout)")
    e1 = press_enter_multimethod_once()
    log(f"RESULT: enter1={e1}")
    return e1

# ---------------------------------------------------------------------

STEP = 5
STEP_FINE = 1

def _drain_keys():
    # eat any pending HighGUI key events so previous keypress isn't reused
    while True:
        k = cv2.waitKey(1)
        if k == -1:
            break

def wait_for_enter(prompt: str, win_title: str = "Press ENTER to mark corner"):
    """
    Robust Enter wait that works both in .py and PyInstaller .exe,
    regardless of which window has focus.
    Tries: global keyboard hook -> tiny cv2 window -> console input().
    """
    print(prompt)

    # 1) Try global hook (works even if no focus; may need admin)
    try:
        import keyboard
        print("  Press ENTER to record current mouse position...")
        keyboard.wait("enter")
        x, y = pyautogui.position()
        return x, y
    except Exception:
        pass

    # 2) Use a tiny OpenCV window to capture Enter with cv2.waitKey
    hint = 255 * np.ones((80, 460, 3), np.uint8)
    cv2.putText(
        hint,
        "Press ENTER to record mouse position (Esc/Q cancel)",
        (10, 45),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    cv2.namedWindow(win_title, cv2.WINDOW_AUTOSIZE)
    try:
        try:
            cv2.setWindowProperty(win_title, cv2.WND_PROP_TOPMOST, 1)
        except Exception:
            pass
        cv2.imshow(win_title, hint)
        _drain_keys()
        while True:
            k = cv2.waitKey(10) & 0xFF
            if k in (13, 10):  # Enter
                break
            if k in (27, ord("q")):  # Cancel
                cv2.destroyWindow(win_title)
                raise KeyboardInterrupt("Cancelled by user")
    finally:
        try:
            cv2.destroyWindow(win_title)
        except Exception:
            pass

    x, y = pyautogui.position()
    return x, y

def clamp_region(x1, y1, x2, y2, mon):
    x1 = max(mon["left"], min(x1, mon["left"] + mon["width"] - 2))
    y1 = max(mon["top"],  min(y1, mon["top"]  + mon["height"] - 2))
    x2 = max(x1 + 1,     min(x2, mon["left"] + mon["width"] - 1))
    y2 = max(y1 + 1,     min(y2, mon["top"]  + mon["height"] - 1))
    return x1, y1, x2, y2

def hud(img_bgr, text, line=0):
    y = 24 + line * 22
    cv2.putText(img_bgr, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,0), 3, cv2.LINE_AA)
    cv2.putText(img_bgr, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 1, cv2.LINE_AA)

def try_focus_window(title_substring: str):
    try:
        import win32gui, win32con
    except Exception:
        return False
    target = None
    def enum_handler(h, _):
        nonlocal target
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if title_substring.lower() in t.lower():
                target = h
    win32gui.EnumWindows(enum_handler, None)
    if not target:
        return False
    try:
        win32gui.ShowWindow(target, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(target)
        return True
    except Exception:
        return False

# ------------------- Live tuner (RAW | EDGES) -------------------
def live_tuner(canny_lo_init: int, canny_hi_init: int):
    pyautogui.FAILSAFE = True
    print("=== Live Region Tuner (RAW | EDGES) ===")
    x1, y1 = wait_for_enter("Set mouse to LEFT-TOP corner of the region.")
    x2, y2 = wait_for_enter("Set mouse to BOTTOM-RIGHT corner of the region.")
    if x2 <= x1 or y2 <= y1:
        raise SystemExit(f"Invalid region. LT=({x1},{y1}) must be above/left of BR=({x2},{y2}).")

    lo, hi = canny_lo_init, canny_hi_init
    os.makedirs("tuner_captures", exist_ok=True)

    with mss.mss() as sct:
        mon = sct.monitors[1]
        x1, y1, x2, y2 = clamp_region(x1, y1, x2, y2, mon)
        use_fine = False

        while True:
            bbox = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
            bgr = np.array(sct.grab(bbox))[:, :, :3]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, lo, hi)

            left  = bgr.copy()
            right = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

            edge_ratio = float(np.mean(edges > 0))
            info1 = f"REGION LT=({x1},{y1}) BR=({x2},{y2}) size={bbox['width']}x{bbox['height']}"
            info2 = f"CANNY lo={lo} hi={hi}  edges={edge_ratio*100:.2f}%"
            info3 = "ARROWS move | I/K H | J/L W | [/] lo | {/} hi | S save | P print | Q/Esc done"
            hud(left,  info1, 0); hud(left,  info2, 1); hud(left,  info3, 2)
            hud(right, info1, 0); hud(right, info2, 1)

            preview = np.hstack([left, right])
            cv2.imshow("Live Region Tuner  (RAW | EDGES)", preview)

            key = cv2.waitKey(1) & 0xFF
            if key == 255:
                continue
            if key in (ord('q'), 27):
                cv2.destroyAllWindows()
                return x1, y1, x2, y2, lo, hi

            if key == ord(';'):
                use_fine = True
                continue
            step = STEP_FINE if use_fine else STEP
            use_fine = False

            # Move/resize
            if key == 81:   x1 -= step; x2 -= step
            elif key == 82: y1 -= step; y2 -= step
            elif key == 83: x1 += step; x2 += step
            elif key == 84: y1 += step; y2 += step
            elif key == ord('j'): x2 -= step
            elif key == ord('l'): x2 += step
            elif key == ord('i'): y2 -= step
            elif key == ord('k'): y2 += step
            # Canny thresholds
            elif key == ord('['):  lo = max(0, lo - 1)
            elif key == ord(']'):  lo = min(255, lo + 1)
            elif key == ord('{'):  hi = max(0, hi - 1)
            elif key == ord('}'):  hi = min(255, hi + 1)
            # Save/print
            elif key == ord('s'):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                cv2.imwrite(os.path.join("tuner_captures", f"raw_{ts}.png"), bgr)
                cv2.imwrite(os.path.join("tuner_captures", f"edges_{ts}.png"), edges)
            elif key == ord('p'):
                print(f"LT=({x1},{y1}) BR=({x2},{y2}) size={x2-x1}x{y2-y1}  lo={lo} hi={hi}")

            x1, y1, x2, y2 = clamp_region(x1, y1, x2, y2, mon)

# ------------------- Scanner (edges > 0%) -------------------
def scanner(x1, y1, x2, y2, lo, hi, focus_title=None):
    """
    Trigger when CURRENT frame has any edges: mean(edges>0) > 0.0
    Action: log every step → Enter, wait 2s, Enter
    Idle Action: if no trigger for 25s, press Enter once.
    """
    pyautogui.FAILSAFE = True
    if focus_title:
        focused = try_focus_window(focus_title)
        log(f"[focus] '{focus_title}' -> {'OK' if focused else 'not found/denied'}")
        time.sleep(0.2)

    bbox = {"left": x1, "top": y1, "width": x2 - x1, "height": y2 - y1}
    interval = 0.03
    cooldown = 0.40     # debounce
    idle_timeout = 25.0 # seconds

    log("=== Scanning (edge ratio > 0.00%) ===")
    log(f"Region LT=({x1},{y1}) BR=({x2},{y2}) size={bbox['width']}x{bbox['height']}")
    log(f"Canny lo={lo} hi={hi}")
    log(f"Idle timeout set to {idle_timeout} seconds.")

    last_trigger_t = time.time() # Start the timer now

    with mss.mss() as sct:
        try:
            while True:
                bgr = np.array(sct.grab(bbox))[:, :, :3]
                gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, lo, hi)

                edge_ratio = float(np.mean(edges > 0))
                now = time.time()

                # 1. Positive trigger
                if edge_ratio > 0.0 and (now - last_trigger_t) >= cooldown:
                    log(f"TRIGGER: edges={edge_ratio*100:.3f}%  → initiating double Enter sequence")
                    ok = press_enter_double_multimethod_logged()
                    log(f"TRIGGER-RESULT: double_enter_sent={ok}")
                    last_trigger_t = time.time()

                # 2. Idle timeout
                elif (now - last_trigger_t) >= idle_timeout:
                    log(f"IDLE: No trigger for {idle_timeout}s → initiating single Enter")
                    ok = press_enter_single_multimethod_logged()
                    log(f"IDLE-RESULT: single_enter_sent={ok}")
                    last_trigger_t = time.time()

                time.sleep(interval)
        except KeyboardInterrupt:
            log("Stopped.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-title", type=str, default=None,
                    help="Bring a window containing this title to foreground before scanning")
    ap.add_argument("--canny-lo", type=int, default=50, help="Canny low threshold (0-255)")
    ap.add_argument("--canny-hi", type=int, default=150, help="Canny high threshold (0-255)")
    args = ap.parse_args()

    x1, y1, x2, y2, lo, hi = live_tuner(args.canny_lo, args.canny_hi)
    scanner(x1, y1, x2, y2, lo, hi, focus_title=args.focus_title)

if __name__ == "__main__":
    main()
