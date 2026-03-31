from pynput.mouse import Button, Controller as Mouse
from pynput.keyboard import Key, Controller as Keyboard

_KEY_MAP = {
    'enter': Key.enter, 'escape': Key.esc, 'esc': Key.esc,
    'space': Key.space, 'tab': Key.tab,
    'backspace': Key.backspace, 'delete': Key.delete,
    'shift': Key.shift, 'ctrl': Key.ctrl, 'alt': Key.alt,
    'win': Key.cmd, 'super': Key.cmd,
    'up': Key.up, 'down': Key.down, 'left': Key.left, 'right': Key.right,
    'home': Key.home, 'end': Key.end,
    'page_up': Key.page_up, 'page_down': Key.page_down,
    'insert': Key.insert, 'caps_lock': Key.caps_lock,
    'f1': Key.f1,  'f2': Key.f2,  'f3': Key.f3,  'f4': Key.f4,
    'f5': Key.f5,  'f6': Key.f6,  'f7': Key.f7,  'f8': Key.f8,
    'f9': Key.f9,  'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
}

_BTN_MAP = {
    'left':   Button.left,
    'right':  Button.right,
    'middle': Button.middle,
}


class InputHandler:
    def __init__(self):
        self._mouse = Mouse()
        self._keyboard = Keyboard()

    # ── Mouse ──────────────────────────────────────────────────────────────

    def mouse_move(self, x: int, y: int):
        self._mouse.position = (x, y)

    def mouse_click(self, x: int, y: int, button: str, pressed: bool):
        self._mouse.position = (x, y)
        btn = _BTN_MAP.get(button, Button.left)
        if pressed:
            self._mouse.press(btn)
        else:
            self._mouse.release(btn)

    def mouse_scroll(self, x: int, y: int, dx: int, dy: int):
        self._mouse.position = (x, y)
        self._mouse.scroll(dx, dy)

    # ── Keyboard ───────────────────────────────────────────────────────────

    def key_press(self, key: str):
        k = self._resolve(key)
        if k is not None:
            self._keyboard.press(k)

    def key_release(self, key: str):
        k = self._resolve(key)
        if k is not None:
            self._keyboard.release(k)

    def _resolve(self, key: str):
        if key in _KEY_MAP:
            return _KEY_MAP[key]
        if len(key) == 1:
            return key
        return None
