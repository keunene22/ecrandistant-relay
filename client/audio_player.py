"""Audio playback avec pré-buffer pour éviter les coupures."""
import queue
import logging

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    import numpy as np
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

PREBUFFER_CHUNKS = 4   # chunks à accumuler avant de commencer la lecture


class AudioPlayer:
    DTYPE = 'int16'

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=80)
        self._stream    = None
        self._samplerate = 48000
        self._channels   = 2
        self._buffered   = 0          # chunks reçus avant le début de la lecture
        self._ready      = False      # pré-buffer atteint ?
        self.available   = _AVAILABLE

    def start(self, samplerate: int = 48000, channels: int = 2):
        if not _AVAILABLE:
            return
        self._samplerate = samplerate
        self._channels   = channels
        self._ready      = False
        self._buffered   = 0
        try:
            self._stream = sd.OutputStream(
                channels=channels,
                samplerate=samplerate,
                dtype=self.DTYPE,
                blocksize=4096,          # gros blocs = moins de coupures
                callback=self._cb,
            )
            self._stream.start()
            logger.info('AudioPlayer démarré : %d Hz  %d ch', samplerate, channels)
        except Exception as e:
            logger.warning('AudioPlayer impossible : %s', e)
            self.available = False

    def _cb(self, outdata, frames, time, status):
        if not self._ready:
            # Pré-buffer : sortir du silence jusqu'à ce que la queue soit remplie
            outdata[:] = 0
            return
        try:
            chunk = self._queue.get_nowait()
            arr   = np.frombuffer(chunk, dtype=self.DTYPE)
            needed = frames * self._channels
            if len(arr) < needed:
                arr = np.pad(arr, (0, needed - len(arr)))
            outdata[:] = arr[:needed].reshape(outdata.shape)
        except queue.Empty:
            outdata[:] = 0   # trou réseau → silence court

    def play(self, pcm: bytes):
        """Reçoit un chunk PCM. Lance la lecture après le pré-buffer."""
        if not self._queue.full():
            self._queue.put_nowait(pcm)

        if not self._ready:
            self._buffered += 1
            if self._buffered >= PREBUFFER_CHUNKS:
                self._ready = True
                logger.info('Pré-buffer atteint — lecture démarrée')

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._ready   = False
        self._buffered = 0
