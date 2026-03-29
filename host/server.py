import asyncio
import json
import logging
import ssl

import websockets
import websockets.exceptions

try:
    import pyperclip
    _CLIP = True
except Exception:
    _CLIP = False

from shared.protocol import (
    decode, encode_frame, encode_audio, encode_json, hash_password,
    MSG_AUTH, MSG_AUTH_OK, MSG_AUTH_FAIL,
    MSG_MOUSE, MSG_KEY, MSG_PING, MSG_PONG,
    MSG_CONFIG, MSG_SELECT_MON, MSG_MON_CHANGED,
    MSG_CLIPBOARD, MSG_AUDIO_CFG,
)
from host.screen_capture import ScreenCapture
from host.input_handler import InputHandler
from host.audio_capture import AudioCapture

logger = logging.getLogger(__name__)


# ── Frame streaming ────────────────────────────────────────────────────────────

async def _send_frames(ws, capture: ScreenCapture, fps_ref: list):
    loop = asyncio.get_running_loop()
    while True:
        fps = fps_ref[0]
        interval = 1.0 / fps
        t0 = loop.time()
        jpeg = await loop.run_in_executor(None, capture.capture)
        await ws.send(encode_frame(jpeg, capture.width, capture.height))
        elapsed = loop.time() - t0
        await asyncio.sleep(max(0.0, interval - elapsed))


# ── Audio streaming ────────────────────────────────────────────────────────────

async def _send_audio(ws, audio_cap: AudioCapture, enabled: asyncio.Event):
    loop = asyncio.get_running_loop()
    while True:
        if not enabled.is_set():
            await asyncio.sleep(0.1)
            continue
        chunk = await loop.run_in_executor(None, audio_cap.get_chunk)
        if chunk and enabled.is_set():
            # Utilise les params de l'INSTANCE (mis à jour au démarrage selon le device)
            await ws.send(encode_audio(chunk, audio_cap.SAMPLE_RATE, audio_cap.CHANNELS))


# ── Clipboard sync (host → client) ────────────────────────────────────────────

async def _sync_clipboard(ws, state: dict, enabled: asyncio.Event):
    """Poll host clipboard every 500 ms; send changes to client."""
    loop = asyncio.get_running_loop()
    while True:
        await asyncio.sleep(0.5)
        if not enabled.is_set() or not _CLIP:
            continue
        try:
            text = await loop.run_in_executor(None, pyperclip.paste)
            if text and text != state['last_sent'] and text != state['last_received']:
                state['last_sent'] = text
                await ws.send(encode_json(MSG_CLIPBOARD, {'text': text}))
        except Exception:
            pass


# ── Input receiving ────────────────────────────────────────────────────────────

async def _recv_input(
    ws,
    handler: InputHandler,
    capture: ScreenCapture,
    fps_ref: list,
    audio_enabled: asyncio.Event,
    clip_state: dict,
    clip_enabled: asyncio.Event,
):
    loop = asyncio.get_running_loop()
    async for data in ws:
        if isinstance(data, str):
            data = data.encode('utf-8')
        try:
            msg = decode(data)
            t = msg.get('_msg_type')

            # ── Mouse ──────────────────────────────────────────────────────
            if t == MSG_MOUSE:
                x, y = int(msg['x']), int(msg['y'])
                ev = msg.get('event')
                if ev == 'move':
                    await loop.run_in_executor(None, handler.mouse_move, x, y)
                elif ev == 'click':
                    await loop.run_in_executor(
                        None, handler.mouse_click,
                        x, y, msg.get('button', 'left'), msg.get('pressed', True),
                    )
                elif ev == 'scroll':
                    await loop.run_in_executor(
                        None, handler.mouse_scroll,
                        x, y, int(msg.get('dx', 0)), int(msg.get('dy', 0)),
                    )

            # ── Keyboard ───────────────────────────────────────────────────
            elif t == MSG_KEY:
                ev = msg.get('event')
                key = msg.get('key', '')
                if ev == 'press':
                    await loop.run_in_executor(None, handler.key_press, key)
                elif ev == 'release':
                    await loop.run_in_executor(None, handler.key_release, key)

            # ── Ping ───────────────────────────────────────────────────────
            elif t == MSG_PING:
                await ws.send(encode_json(MSG_PONG, {}))

            # ── Config (quality, fps) ──────────────────────────────────────
            elif t == MSG_CONFIG:
                if 'quality' in msg:
                    capture.quality = max(10, min(90, int(msg['quality'])))
                if 'fps' in msg:
                    fps_ref[0] = max(1, min(60, int(msg['fps'])))

            # ── Monitor selection ──────────────────────────────────────────
            elif t == MSG_SELECT_MON:
                idx = int(msg.get('index', 1))
                capture.set_monitor(idx)
                await ws.send(encode_json(MSG_MON_CHANGED, {
                    'width': capture.width,
                    'height': capture.height,
                    'index': capture.monitor_index,
                }))

            # ── Clipboard (client → host) ──────────────────────────────────
            elif t == MSG_CLIPBOARD:
                text = msg.get('text', '')
                if text and _CLIP and clip_enabled.is_set():
                    clip_state['last_received'] = text
                    clip_state['last_sent'] = text   # prevent re-echo
                    await loop.run_in_executor(None, pyperclip.copy, text)

            # ── Audio toggle ───────────────────────────────────────────────
            elif t == MSG_AUDIO_CFG:
                if msg.get('enabled'):
                    audio_enabled.set()
                else:
                    audio_enabled.clear()

        except Exception as exc:
            logger.debug('Input error: %s', exc)


# ── Full session (auth + streaming + input) ────────────────────────────────────

async def run_host_session(
    ws,
    password_hash: str,
    capture: ScreenCapture,
    handler: InputHandler,
    fps: int,
    audio_cap: AudioCapture | None = None,
):
    """Works over a direct WebSocket or a relayed one."""

    # ── Auth ───────────────────────────────────────────────────────────────
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        msg = decode(raw)
        if (
            msg.get('_msg_type') != MSG_AUTH
            or hash_password(msg.get('password', '')) != password_hash
        ):
            await ws.send(encode_json(MSG_AUTH_FAIL, {'reason': 'Invalid password'}))
            return
    except asyncio.TimeoutError:
        logger.warning('Auth timeout')
        return

    await ws.send(encode_json(MSG_AUTH_OK, {
        'width':    capture.width,
        'height':   capture.height,
        'monitors': capture.list_monitors(),
        'audio':    audio_cap.available if audio_cap else False,
    }))
    logger.info('Client authenticated — session started')

    # ── Shared state ───────────────────────────────────────────────────────
    fps_ref       = [fps]
    audio_enabled = asyncio.Event()
    clip_enabled  = asyncio.Event()
    clip_enabled.set()            # clipboard sync on by default
    clip_state    = {'last_sent': '', 'last_received': ''}

    # ── Run all coroutines concurrently ────────────────────────────────────
    tasks = [
        _send_frames(ws, capture, fps_ref),
        _recv_input(ws, handler, capture, fps_ref, audio_enabled, clip_state, clip_enabled),
        _sync_clipboard(ws, clip_state, clip_enabled),
    ]
    if audio_cap and audio_cap.available:
        tasks.append(_send_audio(ws, audio_cap, audio_enabled))

    try:
        await asyncio.gather(*tasks)
    except websockets.exceptions.ConnectionClosed:
        pass


# ── SSL helper ─────────────────────────────────────────────────────────────────

def make_ssl_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


# ── Direct mode ────────────────────────────────────────────────────────────────

class RemoteDesktopServer:
    def __init__(
        self,
        password: str,
        host: str = '0.0.0.0',
        port: int = 8765,
        fps: int = 20,
        quality: int = 50,
        ssl_cert: str = '',
        ssl_key: str = '',
    ):
        self._password_hash = hash_password(password)
        self.host = host
        self.port = port
        self.fps = fps
        self._capture = ScreenCapture(quality=quality)
        self._input   = InputHandler()
        # Audio is optional — if sounddevice fails for any reason, server still works
        try:
            self._audio = AudioCapture()
            self._audio.start()
        except Exception as e:
            logger.warning('Audio disabled (sounddevice error): %s', e)
            self._audio = None
        self._ssl_ctx = make_ssl_context(ssl_cert, ssl_key) if ssl_cert and ssl_key else None

    async def start(self):
        async with websockets.serve(
            self._handler, self.host, self.port, ssl=self._ssl_ctx
        ):
            proto = 'wss' if self._ssl_ctx else 'ws'
            logger.info('Direct server on %s://%s:%d', proto, self.host, self.port)
            await asyncio.Future()

    async def _handler(self, ws):
        logger.info('Incoming: %s', ws.remote_address)
        try:
            await run_host_session(
                ws, self._password_hash, self._capture,
                self._input, self.fps, self._audio,
            )
        finally:
            logger.info('Disconnected: %s', ws.remote_address)


# ── Relay mode ─────────────────────────────────────────────────────────────────

class RelayHostSession:
    def __init__(
        self,
        relay_url: str,
        password: str,
        fps: int = 20,
        quality: int = 50,
    ):
        self.relay_url = relay_url
        self._password_hash = hash_password(password)
        self.fps = fps
        self._capture = ScreenCapture(quality=quality)
        self._input   = InputHandler()
        try:
            self._audio = AudioCapture()
            self._audio.start()
        except Exception as e:
            logger.warning('Audio disabled (sounddevice error): %s', e)
            self._audio = None

    async def start(self, on_session_id):
        async with websockets.connect(
            self.relay_url,
            ping_interval=20,
            ping_timeout=60,
            open_timeout=30,
        ) as ws:
            await ws.send(json.dumps({'role': 'host'}))
            resp = json.loads(await ws.recv())
            if resp.get('type') != 'registered':
                raise RuntimeError(f"Relay error: {resp.get('reason', resp)}")

            on_session_id(resp['session_id'])
            resp2 = json.loads(await ws.recv())
            if resp2.get('type') != 'peer_connected':
                raise RuntimeError(f"Unexpected: {resp2}")

            logger.info('Client connected via relay')
            await run_host_session(
                ws, self._password_hash, self._capture,
                self._input, self.fps, self._audio,
            )
