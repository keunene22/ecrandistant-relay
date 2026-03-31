"""Host GUI — dark-themed PyQt6 interface for the HOST machine."""
import sys
import asyncio
import random
import socket
import string
import threading
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QFrame, QStackedWidget,
    QMessageBox, QSizePolicy, QDialog, QTextEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QClipboard


# ── Palette sombre ─────────────────────────────────────────────────────────────

STYLE = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: 'Segoe UI', sans-serif;
    font-size: 13px;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 18px;
}
QPushButton:hover  { background-color: #45475a; }
QPushButton:pressed{ background-color: #585b70; }
QPushButton#primary {
    background-color: #89b4fa;
    color: #1e1e2e;
    font-weight: bold;
}
QPushButton#primary:hover  { background-color: #b4d0fb; }
QPushButton#danger {
    background-color: #f38ba8;
    color: #1e1e2e;
    font-weight: bold;
}
QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 6px 10px;
    color: #cdd6f4;
}
QLabel#title {
    font-size: 22px;
    font-weight: bold;
    color: #89b4fa;
}
QLabel#subtitle {
    font-size: 12px;
    color: #6c7086;
}
QLabel#bigcode {
    font-size: 38px;
    font-weight: bold;
    color: #a6e3a1;
    letter-spacing: 6px;
    background-color: #313244;
    border-radius: 8px;
    padding: 10px 20px;
}
QLabel#info {
    color: #89dceb;
    font-size: 12px;
}
QLabel#status_wait  { color: #f9e2af; font-size: 13px; }
QLabel#status_ok    { color: #a6e3a1; font-size: 13px; }
QLabel#status_err   { color: #f38ba8; font-size: 13px; }
QFrame#card {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 10px;
}
"""


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def _fix_relay_url(url: str) -> str:
    url = url.strip()
    if url.startswith('https://'):
        return 'wss://' + url[8:]
    if url.startswith('http://'):
        return 'ws://' + url[7:]
    if not url.startswith('ws://') and not url.startswith('wss://'):
        return 'wss://' + url
    return url


# ── Worker thread ──────────────────────────────────────────────────────────────

class HostWorker(QThread):
    session_ready   = pyqtSignal(str)   # session_id
    client_joined   = pyqtSignal()
    error_occurred  = pyqtSignal(str)
    log_message     = pyqtSignal(str)
    chat_received   = pyqtSignal(str)   # text from client

    def __init__(self, mode: str, password: str,
                 relay_url: str = '', port: int = 8765,
                 use_tls: bool = False):
        super().__init__()
        self.mode      = mode
        self.password  = password
        self.relay_url = relay_url
        self.port      = port
        self.use_tls   = use_tls
        self._stop_event  = threading.Event()
        self._chat_out    = None   # asyncio.Queue set in _run_relay
        self._loop_ref    = None   # running event loop reference

    def run(self):
        loop = asyncio.new_event_loop()
        self._loop_ref = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run())

    async def _run(self):
        try:
            if self.mode == 'relay':
                await self._run_relay()
            else:
                await self._run_direct()
        except Exception as e:
            self.error_occurred.emit(str(e))

    async def _run_relay(self):
        import websockets, json
        from host.server import run_host_session
        from host.screen_capture import ScreenCapture
        from host.input_handler import InputHandler
        try:
            from host.audio_capture import AudioCapture
            audio = AudioCapture()
            audio.start()
        except Exception:
            audio = None

        capture = ScreenCapture(quality=50)
        inp     = InputHandler()

        # Réveil du relay (Render free tier)
        self.log_message.emit('Réveil du relay…')
        http_url = self.relay_url.replace('wss://', 'https://').replace('ws://', 'http://')
        try:
            import urllib.request
            urllib.request.urlopen(http_url, timeout=60)
        except Exception:
            pass
        self.log_message.emit('Connexion au relay…')

        # Boucle de reconnexion automatique
        while True:
            try:
                async with websockets.connect(
                    self.relay_url,
                    ping_interval=None,
                    open_timeout=30,
                ) as ws:
                    await ws.send(json.dumps({'role': 'host'}))
                    # Attendre 'registered' en ignorant les heartbeats éventuels
                    while True:
                        resp = json.loads(await ws.recv())
                        t = resp.get('type')
                        if t == 'registered':
                            break
                        elif t == 'heartbeat':
                            await ws.send(json.dumps({'type': 'heartbeat_ack'}))
                        elif t == 'error':
                            raise RuntimeError(resp.get('reason', str(resp)))
                        # Ignorer les autres messages inattendus

                    sid = resp['session_id']
                    self.session_ready.emit(sid)
                    self.log_message.emit(f'Session ID: {sid} — en attente du client…')

                    # Attente du client avec keepalives bidirectionnels
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                            msg = json.loads(raw)
                            t = msg.get('type')
                            if t == 'peer_connected':
                                break
                            elif t == 'heartbeat':
                                await ws.send(json.dumps({'type': 'heartbeat_ack'}))
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({'type': 'keepalive'}))

                    # Client connecté — démarrage de la session
                    self.client_joined.emit()
                    self.log_message.emit('Client connecté ! Démarrage de la session…')
                    self._chat_out = asyncio.Queue()
                    await run_host_session(
                        ws, self._hash(), capture, inp, 20, audio,
                        on_chat_recv=lambda t, s: self.chat_received.emit(t),
                        chat_out_queue=self._chat_out,
                    )
                    # Session terminée — reconnecter pour accepter un nouveau client
                    self.log_message.emit('Session terminée — reconnexion au relay…')

            except Exception as e:
                # Connexion perdue → reconnexion automatique dans 3s
                self.log_message.emit(f'Reconnexion dans 3s… ({e})')
                await asyncio.sleep(3)
                self.log_message.emit('Reconnexion au relay…')

    async def _run_direct(self):
        from host.server import RemoteDesktopServer
        ssl_cert = ssl_key = ''
        if self.use_tls:
            import os
            ssl_cert = os.path.join(os.path.dirname(__file__), 'cert.pem')
            ssl_key  = os.path.join(os.path.dirname(__file__), 'key.pem')
        server = RemoteDesktopServer(
            password=self.password, port=self.port, fps=20, quality=50,
            ssl_cert=ssl_cert, ssl_key=ssl_key,
        )
        self.log_message.emit(f'En écoute sur le port {self.port}…')
        await server.start()

    def send_chat(self, text: str):
        """Send a chat message to the connected client."""
        if self._chat_out and self._loop_ref and self._loop_ref.is_running():
            import time as _time
            asyncio.run_coroutine_threadsafe(
                self._chat_out.put({'text': text, 'sender': 'host',
                                    'ts': int(_time.time())}),
                self._loop_ref,
            )

    def _hash(self):
        from host.server import hash_password
        return hash_password(self.password)


# ── Chat dialog (host side) ────────────────────────────────────────────────────

class HostChatDialog(QDialog):
    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.setWindowTitle('💬 Chat')
        self.setMinimumSize(360, 380)
        self._worker = worker

        lay = QVBoxLayout(self)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            'background:#1e1e2e;color:#cdd6f4;border:1px solid #313244;border-radius:6px;'
        )
        lay.addWidget(self._log)

        row = QHBoxLayout()
        self._inp = QLineEdit()
        self._inp.setPlaceholderText('Message…')
        self._inp.setStyleSheet(
            'background:#313244;color:#cdd6f4;border:1px solid #45475a;'
            'border-radius:5px;padding:6px 10px;'
        )
        self._inp.returnPressed.connect(self._send)
        btn = QPushButton('Envoyer')
        btn.setStyleSheet(
            'background:#89b4fa;color:#1e1e2e;font-weight:bold;'
            'border-radius:5px;padding:6px 14px;'
        )
        btn.clicked.connect(self._send)
        row.addWidget(self._inp)
        row.addWidget(btn)
        lay.addLayout(row)

    def _send(self):
        text = self._inp.text().strip()
        if not text:
            return
        self._worker.send_chat(text)
        self._append(text, 'host')
        self._inp.clear()

    def append_recv(self, text: str):
        self._append(text, 'client')

    def _append(self, text: str, sender: str):
        import time as _t
        color = '#89b4fa' if sender == 'client' else '#a6e3a1'
        label = 'Client' if sender == 'client' else 'Vous'
        ts = _t.strftime('%H:%M')
        self._log.append(
            f'<span style="color:{color};font-weight:bold">[{ts}] {label}:</span>'
            f' <span style="color:#cdd6f4">{text}</span>'
        )


# ── Carte d'information (session) ──────────────────────────────────────────────

class InfoCard(QFrame):
    def __init__(self, label: str, value: str, copyable: bool = True):
        super().__init__()
        self.setObjectName('card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setObjectName('subtitle')
        top.addWidget(lbl)
        top.addStretch()
        if copyable:
            btn = QPushButton('📋 Copier')
            btn.setFixedWidth(90)
            btn.clicked.connect(lambda: QApplication.clipboard().setText(value))
            top.addWidget(btn)
        lay.addLayout(top)

        val = QLabel(value)
        if label == 'SESSION ID':
            val.setObjectName('bigcode')
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            val.setFont(QFont('Segoe UI', 16, QFont.Weight.Bold))
            val.setStyleSheet('color: #f9e2af; letter-spacing: 4px;')
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(val)


# ── Fenêtre principale ─────────────────────────────────────────────────────────

class HostWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('EcranDistant — HOST')
        self.setMinimumWidth(480)
        self.setStyleSheet(STYLE)
        self._worker: HostWorker | None = None
        self._password = ''.join(random.choices(string.digits, k=6))

        self._stack = QStackedWidget()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._stack)

        self._chat_dialog: HostChatDialog | None = None

        self._stack.addWidget(self._build_menu())    # 0
        self._stack.addWidget(self._build_relay())   # 1
        self._stack.addWidget(self._build_direct())  # 2
        self._stack.addWidget(self._build_waiting()) # 3
        self._stack.addWidget(self._build_session()) # 4

    # ── Pages ──────────────────────────────────────────────────────────────────

    def _build_menu(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(18)

        title = QLabel('EcranDistant')
        title.setObjectName('title')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        sub = QLabel('Bureau à distance • Choisissez un mode')
        sub.setObjectName('subtitle')
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(20)

        btn_relay = QPushButton('🌐  Relay Internet  (deux réseaux différents)')
        btn_relay.setObjectName('primary')
        btn_relay.setMinimumHeight(50)
        btn_relay.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        lay.addWidget(btn_relay)

        btn_direct = QPushButton('🏠  Direct LAN  (même réseau)')
        btn_direct.setMinimumHeight(50)
        btn_direct.clicked.connect(lambda: self._stack.setCurrentIndex(2))
        lay.addWidget(btn_direct)

        lay.addStretch()
        pw_row = QHBoxLayout()
        pw_row.addWidget(QLabel('Mot de passe généré :'))
        self._pw_lbl = QLabel(self._password)
        self._pw_lbl.setStyleSheet('color:#f9e2af; font-weight:bold; font-size:16px; letter-spacing:3px;')
        pw_row.addWidget(self._pw_lbl)
        pw_row.addStretch()
        lay.addLayout(pw_row)
        return w

    def _build_relay(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(14)

        lay.addWidget(QLabel('🌐  Mode Relay — URL du serveur relay :'))
        self._relay_edit = QLineEdit('wss://api.194.163.188.237.nip.io/relay')
        lay.addWidget(self._relay_edit)

        row = QHBoxLayout()
        back = QPushButton('← Retour')
        back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        start = QPushButton('Démarrer')
        start.setObjectName('primary')
        start.clicked.connect(self._start_relay)
        row.addWidget(back)
        row.addStretch()
        row.addWidget(start)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _build_direct(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(14)

        ip = get_local_ip()
        lay.addWidget(QLabel('🏠  Mode Direct LAN'))
        lay.addSpacing(10)
        lay.addWidget(InfoCard('VOTRE IP', ip))
        lay.addWidget(InfoCard('PORT', '8765'))

        row = QHBoxLayout()
        back = QPushButton('← Retour')
        back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        start = QPushButton('Démarrer')
        start.setObjectName('primary')
        start.clicked.connect(self._start_direct)
        row.addWidget(back)
        row.addStretch()
        row.addWidget(start)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _build_waiting(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(16)
        lay.addStretch()

        lbl = QLabel('⏳  En attente du client…')
        lbl.setObjectName('status_wait')
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(lbl)

        self._dots_lbl = QLabel('.')
        self._dots_lbl.setObjectName('subtitle')
        self._dots_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._dots_lbl)

        self._dots_timer = QTimer()
        self._dots_timer.timeout.connect(self._animate_dots)
        self._dots_count = 0
        self._dots_timer.start(500)

        lay.addStretch()
        stop_btn = QPushButton('⏹  Arrêter')
        stop_btn.setObjectName('danger')
        stop_btn.clicked.connect(self._stop)
        lay.addWidget(stop_btn)
        return w

    def _build_session(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 40, 40, 40)
        lay.setSpacing(16)

        self._status_lbl = QLabel('⏳  En attente du client…')
        self._status_lbl.setObjectName('status_wait')
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._status_lbl)

        lay.addSpacing(10)
        self._sid_card  = InfoCard('SESSION ID', '------')
        self._pass_card = InfoCard('MOT DE PASSE', self._password)
        lay.addWidget(self._sid_card)
        lay.addWidget(self._pass_card)

        info = QLabel('💡  Partage le Session ID et le mot de passe avec l\'autre personne')
        info.setObjectName('info')
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setWordWrap(True)
        lay.addWidget(info)

        lay.addStretch()

        btn_row = QHBoxLayout()
        self._chat_btn = QPushButton('💬  Chat')
        self._chat_btn.clicked.connect(self._open_chat)
        btn_row.addWidget(self._chat_btn)

        stop_btn = QPushButton('⏹  Arrêter')
        stop_btn.setObjectName('danger')
        stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(stop_btn)
        lay.addLayout(btn_row)
        return w

    # ── Actions ────────────────────────────────────────────────────────────────

    def _animate_dots(self):
        self._dots_count = (self._dots_count + 1) % 4
        self._dots_lbl.setText('.' * (self._dots_count + 1))

    def _start_relay(self):
        relay_url = _fix_relay_url(self._relay_edit.text())
        self._stack.setCurrentIndex(3)  # waiting page

        # Rebuild session page with correct password
        self._refresh_session_page()

        self._worker = HostWorker(
            mode='relay', password=self._password, relay_url=relay_url
        )
        self._worker.session_ready.connect(self._on_session_ready)
        self._worker.client_joined.connect(self._on_client_joined)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.log_message.connect(lambda m: logging.info(m))
        self._worker.chat_received.connect(self._on_chat_received)
        self._worker.start()

    def _start_direct(self):
        self._stack.setCurrentIndex(3)
        self._worker = HostWorker(
            mode='direct', password=self._password, port=8765
        )
        self._worker.client_joined.connect(self._on_client_joined)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.log_message.connect(lambda m: logging.info(m))
        self._worker.start()

    def _refresh_session_page(self):
        # Rebuild the session info cards with current password
        self._pass_card = InfoCard('MOT DE PASSE', self._password)

    def _on_session_ready(self, sid: str):
        # Rebuild session page with session ID
        page = self._stack.widget(4)
        lay = page.layout()

        # Remove old cards (indices 2 and 3 after status + spacing)
        for i in reversed(range(lay.count())):
            item = lay.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if isinstance(w, InfoCard):
                    lay.removeWidget(w)
                    w.deleteLater()

        sid_card  = InfoCard('SESSION ID', sid)
        pass_card = InfoCard('MOT DE PASSE', self._password)
        lay.insertWidget(2, pass_card)
        lay.insertWidget(2, sid_card)

        self._stack.setCurrentIndex(4)

        # Auto-copy session ID
        QApplication.clipboard().setText(sid)

    def _open_chat(self):
        if self._worker is None:
            return
        if self._chat_dialog is None:
            self._chat_dialog = HostChatDialog(self._worker, self)
        self._chat_dialog.show()
        self._chat_dialog.raise_()

    def _on_chat_received(self, text: str):
        if self._chat_dialog is None and self._worker:
            self._chat_dialog = HostChatDialog(self._worker, self)
        if self._chat_dialog:
            self._chat_dialog.append_recv(text)
            self._chat_dialog.show()
            self._chat_dialog.raise_()
        # Also flash the chat button
        self._chat_btn.setText('💬  Chat ●')
        QTimer.singleShot(3000, lambda: self._chat_btn.setText('💬  Chat'))

    def _on_client_joined(self):
        self._status_lbl.setText('✅  Client connecté — session en cours')
        self._status_lbl.setObjectName('status_ok')
        self._status_lbl.setStyleSheet('color: #a6e3a1; font-size: 13px;')

    def _on_error(self, msg: str):
        self._dots_timer.stop()
        QMessageBox.critical(self, 'Erreur', msg)
        self._stack.setCurrentIndex(0)

    def _stop(self):
        if self._worker:
            self._worker.terminate()
            self._worker = None
        self._dots_timer.stop()
        self._stack.setCurrentIndex(0)

    def closeEvent(self, event):
        if self._worker:
            self._worker.terminate()
        event.accept()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('EcranDistant HOST')
    w = HostWindow()
    w.resize(500, 420)
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
