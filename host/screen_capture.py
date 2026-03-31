import io
import threading
import mss
from PIL import Image

# Each thread gets its own mss context (Win32 handles are thread-local).
_tls = threading.local()


class ScreenCapture:
    def __init__(self, monitor_index: int = 1, quality: int = 50):
        self.monitor_index = monitor_index
        self.quality = quality
        self._lock = threading.Lock()
        self._set_monitor_by_index(monitor_index)

    # ── Monitor management ─────────────────────────────────────────────────

    def _set_monitor_by_index(self, index: int):
        with mss.mss() as sct:
            monitors = sct.monitors          # index 0 = all screens combined
            if index < 1 or index >= len(monitors):
                index = 1
            m = monitors[index]
            with self._lock:
                self.monitor_index = index
                self.width: int = m['width']
                self.height: int = m['height']
                self._monitor_dict: dict = dict(m)

    def set_monitor(self, index: int):
        """Switch to a different monitor (thread-safe)."""
        self._set_monitor_by_index(index)

    @staticmethod
    def list_monitors() -> list:
        """Return info about all available monitors (excluding index 0)."""
        with mss.mss() as sct:
            return [
                {
                    'index': i,
                    'width': m['width'],
                    'height': m['height'],
                    'name': f'Monitor {i}',
                }
                for i, m in enumerate(sct.monitors)
                if i >= 1          # skip index 0 (virtual "all monitors" screen)
            ]

    # ── Capture ────────────────────────────────────────────────────────────

    def capture(self) -> bytes:
        """Capture current monitor and return compressed JPEG bytes.
        Safe to call from any thread — each thread owns its mss context.
        """
        if not hasattr(_tls, 'sct'):
            _tls.sct = mss.mss()

        with self._lock:
            monitor = dict(self._monitor_dict)
            quality = self.quality

        shot = _tls.sct.grab(monitor)
        img = Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=False)
        return buf.getvalue()
