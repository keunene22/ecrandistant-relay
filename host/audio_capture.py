"""System audio capture — WASAPI loopback ou Stereo Mix (Windows)."""
import queue
import logging

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False
    logger.warning('sounddevice introuvable — audio désactivé')


def _find_wasapi_output() -> int | None:
    """Trouve le device WASAPI output correspondant au périphérique par défaut."""
    try:
        hostapis = sd.query_hostapis()
        wasapi_id = next(
            (i for i, h in enumerate(hostapis) if 'WASAPI' in h['name']), None
        )
        if wasapi_id is None:
            return None

        default_name = sd.query_devices(sd.default.device[1])['name'][:10].lower()
        devices = sd.query_devices()

        # Cherche le WASAPI output dont le nom ressemble au default output
        for i, d in enumerate(devices):
            if d['hostapi'] == wasapi_id and d['max_output_channels'] > 0:
                if default_name in d['name'].lower():
                    return i

        # Fallback : n'importe quel WASAPI output
        for i, d in enumerate(devices):
            if d['hostapi'] == wasapi_id and d['max_output_channels'] > 0:
                return i
    except Exception:
        pass
    return None


def _find_stereo_mix() -> int | None:
    """Trouve Mixage stéréo / Stereo Mix — capture directe du son joué."""
    try:
        for i, d in enumerate(sd.query_devices()):
            n = d['name'].lower()
            if d['max_input_channels'] > 0 and any(
                kw in n for kw in ('stereo mix', 'mixage', 'what u hear', 'loopback')
            ):
                return i
    except Exception:
        pass
    return None


class AudioCapture:
    CHANNELS   = 2
    BLOCK_SIZE = 4096    # gros blocs = réseau plus fluide
    DTYPE      = 'int16'

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=60)
        self._stream   = None
        self.available = _AVAILABLE
        self.SAMPLE_RATE = 48000   # mis à jour au démarrage

    def start(self):
        if not _AVAILABLE:
            return

        # ── Stratégie 1 : WASAPI loopback ─────────────────────────────────
        wasapi_idx = _find_wasapi_output()
        if wasapi_idx is not None:
            try:
                sr = int(sd.query_devices(wasapi_idx)['default_samplerate'])
                self.SAMPLE_RATE = sr
                self._stream = sd.InputStream(
                    device=wasapi_idx,
                    channels=self.CHANNELS,
                    samplerate=sr,
                    dtype=self.DTYPE,
                    blocksize=self.BLOCK_SIZE,
                    callback=self._cb,
                    extra_settings=sd.WasapiSettings(loopback=True),
                )
                self._stream.start()
                logger.info('Audio : WASAPI loopback device=%d "%s" %d Hz',
                            wasapi_idx, sd.query_devices(wasapi_idx)['name'], sr)
                return
            except Exception:
                pass   # Pas de WASAPI loopback → on essaie Stereo Mix

        # ── Stratégie 2 : Mixage stéréo / Stereo Mix ──────────────────────
        mix_idx = _find_stereo_mix()
        if mix_idx is not None:
            try:
                sr = int(sd.query_devices(mix_idx)['default_samplerate'])
                self.SAMPLE_RATE = sr
                self._stream = sd.InputStream(
                    device=mix_idx,
                    channels=min(self.CHANNELS, int(sd.query_devices(mix_idx)['max_input_channels'])),
                    samplerate=sr,
                    dtype=self.DTYPE,
                    blocksize=self.BLOCK_SIZE,
                    callback=self._cb,
                )
                self._stream.start()
                logger.info('Audio : Stereo Mix device=%d "%s" %d Hz',
                            mix_idx, sd.query_devices(mix_idx)['name'], sr)
                return
            except Exception as e:
                logger.warning('Stereo Mix échoué (%s)', e)

        # ── Stratégie 3 : micro par défaut (fallback) ─────────────────────
        try:
            self._stream = sd.InputStream(
                channels=self.CHANNELS,
                samplerate=self.SAMPLE_RATE,
                dtype=self.DTYPE,
                blocksize=self.BLOCK_SIZE,
                callback=self._cb,
            )
            self._stream.start()
            logger.info('Audio : microphone par défaut %d Hz', self.SAMPLE_RATE)
        except Exception as e:
            logger.warning('Audio capture impossible : %s', e)
            self.available = False

    def _cb(self, indata, frames, time, status):
        if not self._queue.full():
            self._queue.put_nowait(bytes(indata))

    def get_chunk(self, timeout: float = 0.1) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
