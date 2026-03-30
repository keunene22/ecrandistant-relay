import asyncio
import json
import ssl
import tempfile
import threading
import logging
import time

import websockets
import websockets.exceptions

from PyQt6.QtCore import QObject, pyqtSignal

from shared.protocol import (
    decode, encode_json, encode_file_chunk, hash_password,
    MSG_AUTH, MSG_AUTH_OK, MSG_AUTH_FAIL,
    MSG_MOUSE, MSG_KEY, MSG_PING,
    MSG_CONFIG, MSG_SELECT_MON, MSG_MON_CHANGED,
    MSG_CLIPBOARD, MSG_AUDIO_CFG,
    MSG_FILE_SEND_REQ, MSG_FILE_SEND_ACK, MSG_FILE_CHUNK,
    MSG_FILE_DONE, MSG_FILE_ABORT, MSG_FILE_BROWSE,
    MSG_FILE_LIST, MSG_FILE_GET_REQ, MSG_FILE_GET_INFO,
    MSG_CHAT,
)
from client.audio_player import AudioPlayer

logger = logging.getLogger(__name__)
_MAX_MSG = 10 * 1024 * 1024


class ConnectionWorker(QObject):
    # ── Signals ────────────────────────────────────────────────────────────
    frame_received     = pyqtSignal(bytes, int, int)      # jpeg, w, h
    connected          = pyqtSignal(int, int, list, bool) # w, h, monitors, has_audio
    disconnected       = pyqtSignal(str)
    auth_failed        = pyqtSignal(str)
    monitor_changed    = pyqtSignal(int, int, int)        # w, h, index
    clipboard_received = pyqtSignal(str)                  # text from host
    audio_chunk        = pyqtSignal(bytes, int, int)      # pcm, samplerate, channels

    # File transfer signals
    file_upload_ack    = pyqtSignal(int, bool, str)       # tid, ok, reason
    file_upload_prog   = pyqtSignal(int, int, int)        # tid, sent, total
    file_upload_done   = pyqtSignal(int)                  # tid
    file_upload_abort  = pyqtSignal(int, str)             # tid, reason
    file_list_received = pyqtSignal(int, str, str, list)  # tid, path, parent, entries
    file_dl_info       = pyqtSignal(int, str, int)        # tid, name, size
    file_dl_prog       = pyqtSignal(int, int, int)        # tid, recv, total
    file_dl_done       = pyqtSignal(int, str)             # tid, name
    file_dl_abort      = pyqtSignal(int, str)             # tid, reason

    # Chat signal
    chat_received      = pyqtSignal(str, str, int)        # text, sender, timestamp

    def __init__(
        self,
        password: str,
        *,
        host: str = '',
        port: int = 8765,
        relay_url: str = '',
        session_id: str = '',
        use_tls: bool = False,
    ):
        super().__init__()
        self.password   = password
        self.host       = host
        self.port       = port
        self.relay_url  = relay_url
        self.session_id = session_id
        self.use_tls    = use_tls
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws   = None
        self._running = False
        self._audio = AudioPlayer()
        # tid → {'name': str, 'size': int, 'tmpfile': TemporaryFile, 'received': int}
        self._downloads: dict = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        threading.Thread(target=self._run, daemon=True, name='ws-client').start()

    def stop(self):
        self._running = False
        self._audio.stop()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── Background thread ──────────────────────────────────────────────────

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except Exception as exc:
            self.disconnected.emit(str(exc))

    async def _connect(self):
        if self.relay_url:
            await self._connect_relay()
        else:
            await self._connect_direct()

    # ── Direct ─────────────────────────────────────────────────────────────

    async def _connect_direct(self):
        scheme = 'wss' if self.use_tls else 'ws'
        uri = f'{scheme}://{self.host}:{self.port}'
        ssl_ctx = None
        if self.use_tls:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            async with websockets.connect(uri, ssl=ssl_ctx, max_size=_MAX_MSG) as ws:
                await self._run_session(ws)
        except websockets.exceptions.ConnectionClosed as e:
            self.disconnected.emit(f'Connection closed: {e}')
        except OSError as e:
            self.disconnected.emit(f'Cannot reach host: {e}')

    # ── Relay ──────────────────────────────────────────────────────────────

    async def _connect_relay(self):
        try:
            async with websockets.connect(self.relay_url, max_size=_MAX_MSG) as ws:
                await ws.send(json.dumps({'role': 'client', 'session_id': self.session_id}))
                resp = json.loads(await ws.recv())
                if resp.get('type') == 'error':
                    self.disconnected.emit(resp.get('reason', 'Relay error'))
                    return
                await self._run_session(ws)
        except websockets.exceptions.ConnectionClosed as e:
            self.disconnected.emit(f'Relay closed: {e}')
        except OSError as e:
            self.disconnected.emit(f'Cannot reach relay: {e}')

    # ── Session ────────────────────────────────────────────────────────────

    async def _run_session(self, ws):
        self._ws = ws
        await ws.send(encode_json(MSG_AUTH, {'password': self.password}))

        raw  = await ws.recv()
        resp = decode(raw)
        if resp.get('_msg_type') == MSG_AUTH_FAIL:
            self.auth_failed.emit(resp.get('reason', 'Authentication failed'))
            return

        rw       = resp.get('width', 1920)
        rh       = resp.get('height', 1080)
        monitors = resp.get('monitors', [])
        has_audio= resp.get('audio', False)
        self.connected.emit(rw, rh, monitors, has_audio)
        logger.info('Connected — %dx%d — audio=%s', rw, rh, has_audio)

        async for data in ws:
            if not self._running:
                break
            msg = decode(data)

            if msg.get('type') == 'frame':
                self.frame_received.emit(msg['jpeg'], msg['w'], msg['h'])

            elif msg.get('type') == 'audio':
                logger.info('Audio reçu : %d octets  %d Hz  %d ch',
                            len(msg['pcm']), msg['samplerate'], msg['channels'])
                self.audio_chunk.emit(msg['pcm'], msg['samplerate'], msg['channels'])

            elif msg.get('_msg_type') == MSG_MON_CHANGED:
                self.monitor_changed.emit(
                    msg['width'], msg['height'], msg.get('index', 1)
                )
            elif msg.get('_msg_type') == MSG_CLIPBOARD:
                self.clipboard_received.emit(msg.get('text', ''))

            # ── Chat ───────────────────────────────────────────────────────
            elif msg.get('_msg_type') == MSG_CHAT:
                self.chat_received.emit(
                    msg.get('text', ''),
                    msg.get('sender', 'host'),
                    msg.get('ts', 0),
                )

            # ── File: upload ack ───────────────────────────────────────────
            elif msg.get('_msg_type') == MSG_FILE_SEND_ACK:
                tid = msg.get('id', 0)
                self.file_upload_ack.emit(tid, bool(msg.get('ok')), msg.get('reason', ''))

            # ── File: download info ────────────────────────────────────────
            elif msg.get('_msg_type') == MSG_FILE_GET_INFO:
                tid  = msg.get('id', 0)
                name = msg.get('name', 'file')
                size = int(msg.get('size', 0))
                tmp  = tempfile.NamedTemporaryFile(delete=False, suffix='.tmp')
                self._downloads[tid] = {'name': name, 'size': size,
                                        'tmpfile': tmp, 'received': 0}
                self.file_dl_info.emit(tid, name, size)

            # ── File: download chunk ───────────────────────────────────────
            elif msg.get('type') == 'file_chunk':
                tid = msg['id']
                if tid in self._downloads:
                    dl = self._downloads[tid]
                    dl['tmpfile'].write(msg['data'])
                    dl['received'] += len(msg['data'])
                    self.file_dl_prog.emit(tid, dl['received'], dl['size'])

            # ── File: done (for downloads) ─────────────────────────────────
            elif msg.get('_msg_type') == MSG_FILE_DONE:
                tid  = msg.get('id', 0)
                name = msg.get('name', '')
                if tid in self._downloads:
                    self._downloads[tid]['tmpfile'].close()
                    self.file_dl_done.emit(tid, name)

            # ── File: abort ────────────────────────────────────────────────
            elif msg.get('_msg_type') == MSG_FILE_ABORT:
                tid = msg.get('id', 0)
                if tid in self._downloads:
                    try:
                        self._downloads[tid]['tmpfile'].close()
                    except Exception:
                        pass
                    del self._downloads[tid]
                self.file_dl_abort.emit(tid, msg.get('reason', ''))

            # ── File: directory listing ────────────────────────────────────
            elif msg.get('_msg_type') == MSG_FILE_LIST:
                self.file_list_received.emit(
                    msg.get('id', 0),
                    msg.get('path', ''),
                    msg.get('parent', ''),
                    msg.get('entries', []),
                )

    # ── Thread-safe send helpers ───────────────────────────────────────────

    def _send(self, data: bytes):
        if self._ws and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._ws.send(data), self._loop)

    def send_mouse(self, event: str, x: int, y: int, **kw):
        self._send(encode_json(MSG_MOUSE, {'event': event, 'x': x, 'y': y, **kw}))

    def send_key(self, event: str, key: str):
        self._send(encode_json(MSG_KEY, {'event': event, 'key': key}))

    def send_ping(self):
        self._send(encode_json(MSG_PING, {}))

    def select_monitor(self, index: int):
        self._send(encode_json(MSG_SELECT_MON, {'index': index}))

    def set_audio_enabled(self, enabled: bool):
        self._send(encode_json(MSG_AUDIO_CFG, {'enabled': enabled}))
        if not enabled:
            self._audio.stop()   # coupe la lecture immédiatement

    def send_clipboard(self, text: str):
        self._send(encode_json(MSG_CLIPBOARD, {'text': text}))

    # ── Chat ──────────────────────────────────────────────────────────────

    def send_chat(self, text: str):
        self._send(encode_json(MSG_CHAT, {
            'text': text, 'sender': 'client', 'ts': int(time.time())
        }))

    # ── File transfer ──────────────────────────────────────────────────────

    def browse_host(self, req_id: int, path: str):
        self._send(encode_json(MSG_FILE_BROWSE, {'id': req_id, 'path': path}))

    def request_download(self, transfer_id: int, path: str):
        self._send(encode_json(MSG_FILE_GET_REQ, {'id': transfer_id, 'path': path}))

    def send_file(self, transfer_id: int, file_path: str):
        """Start uploading a file to the host (async, chunked)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._upload_file(transfer_id, file_path), self._loop
            )

    async def _upload_file(self, transfer_id: int, file_path: str):
        import os
        CHUNK = 65536
        loop  = asyncio.get_running_loop()
        try:
            size = os.path.getsize(file_path)
            name = os.path.basename(file_path)
            await self._ws.send(encode_json(MSG_FILE_SEND_REQ,
                                            {'id': transfer_id, 'name': name, 'size': size}))
            sent = 0
            with open(file_path, 'rb') as f:
                while True:
                    chunk = await loop.run_in_executor(None, f.read, CHUNK)
                    if not chunk:
                        break
                    await self._ws.send(encode_file_chunk(transfer_id, chunk))
                    sent += len(chunk)
                    self.file_upload_prog.emit(transfer_id, sent, size)
            await self._ws.send(encode_json(MSG_FILE_DONE,
                                            {'id': transfer_id, 'name': name}))
            self.file_upload_done.emit(transfer_id)
        except Exception as exc:
            try:
                await self._ws.send(encode_json(MSG_FILE_ABORT,
                                                {'id': transfer_id, 'reason': str(exc)}))
            except Exception:
                pass
            self.file_upload_abort.emit(transfer_id, str(exc))

    def get_download_path(self, transfer_id: int) -> str:
        """Return temp file path for a completed download (consumes the entry)."""
        dl = self._downloads.pop(transfer_id, None)
        return dl['tmpfile'].name if dl else ''

    def set_quality(self, quality: int):
        self._send(encode_json(MSG_CONFIG, {'quality': quality}))

    def set_fps(self, fps: int):
        self._send(encode_json(MSG_CONFIG, {'fps': fps}))

    # ── Audio passthrough ──────────────────────────────────────────────────

    def play_audio(self, pcm: bytes, samplerate: int, channels: int):
        if not self._audio._stream:
            logger.info('Démarrage AudioPlayer : %d Hz  %d ch', samplerate, channels)
            self._audio.start(samplerate, channels)
            if not self._audio._stream:
                logger.warning('AudioPlayer échec — stream non créé')
                return
        self._audio.play(pcm)
