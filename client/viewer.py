import os
import shutil
import time

from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSlot
from PyQt6.QtGui  import QImage, QPixmap, QKeyEvent, QCloseEvent, QAction
from PyQt6.QtWidgets import (
    QMainWindow, QLabel, QMessageBox,
    QToolBar, QComboBox, QSlider, QWidget, QSizePolicy,
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QLineEdit, QTextEdit, QFileDialog,
    QSplitter, QApplication,
)

try:
    from PyQt6.QtWidgets import QApplication
    _QT_CLIP = True
except Exception:
    _QT_CLIP = False

# Qt key → pynput-compatible string
_QT_KEY_MAP = {
    Qt.Key.Key_Return:    'enter',
    Qt.Key.Key_Enter:     'enter',
    Qt.Key.Key_Escape:    'escape',
    Qt.Key.Key_Space:     'space',
    Qt.Key.Key_Tab:       'tab',
    Qt.Key.Key_Backtab:   'tab',
    Qt.Key.Key_Backspace: 'backspace',
    Qt.Key.Key_Delete:    'delete',
    Qt.Key.Key_Shift:     'shift',
    Qt.Key.Key_Control:   'ctrl',
    Qt.Key.Key_Alt:       'alt',
    Qt.Key.Key_Meta:      'win',
    Qt.Key.Key_Up:        'up',
    Qt.Key.Key_Down:      'down',
    Qt.Key.Key_Left:      'left',
    Qt.Key.Key_Right:     'right',
    Qt.Key.Key_Home:      'home',
    Qt.Key.Key_End:       'end',
    Qt.Key.Key_PageUp:    'page_up',
    Qt.Key.Key_PageDown:  'page_down',
    Qt.Key.Key_Insert:    'insert',
    Qt.Key.Key_CapsLock:  'caps_lock',
    Qt.Key.Key_F1:  'f1',  Qt.Key.Key_F2:  'f2',  Qt.Key.Key_F3:  'f3',
    Qt.Key.Key_F4:  'f4',  Qt.Key.Key_F5:  'f5',  Qt.Key.Key_F6:  'f6',
    Qt.Key.Key_F7:  'f7',  Qt.Key.Key_F8:  'f8',  Qt.Key.Key_F9:  'f9',
    Qt.Key.Key_F10: 'f10', Qt.Key.Key_F11: 'f11', Qt.Key.Key_F12: 'f12',
}


# ── Chat dialog ────────────────────────────────────────────────────────────────

class ChatDialog(QDialog):
    def __init__(self, connection, parent=None):
        super().__init__(parent)
        self.setWindowTitle('💬 Chat')
        self.setMinimumSize(360, 420)
        self._conn = connection

        lay = QVBoxLayout(self)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            'background:#1e1e2e; color:#cdd6f4; border:1px solid #313244; border-radius:6px;'
        )
        lay.addWidget(self._log)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText('Message…')
        self._input.setStyleSheet(
            'background:#313244; color:#cdd6f4; border:1px solid #45475a;'
            'border-radius:5px; padding:6px 10px;'
        )
        self._input.returnPressed.connect(self._send)
        btn = QPushButton('Envoyer')
        btn.setStyleSheet(
            'background:#89b4fa; color:#1e1e2e; font-weight:bold;'
            'border-radius:5px; padding:6px 14px;'
        )
        btn.clicked.connect(self._send)
        row.addWidget(self._input)
        row.addWidget(btn)
        lay.addLayout(row)

        connection.chat_received.connect(self._on_recv)

    def _send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._conn.send_chat(text)
        self._append(text, 'vous')
        self._input.clear()

    def _on_recv(self, text: str, sender: str, _ts: int):
        self._append(text, 'host')
        if not self.isVisible():
            self.show()
            self.raise_()

    def _append(self, text: str, sender: str):
        color = '#89b4fa' if sender == 'host' else '#a6e3a1'
        label = 'Host' if sender == 'host' else 'Vous'
        t = time.strftime('%H:%M')
        self._log.append(
            f'<span style="color:{color};font-weight:bold">[{t}] {label}:</span>'
            f' <span style="color:#cdd6f4">{text}</span>'
        )


# ── File transfer dialog ────────────────────────────────────────────────────────

class FileTransferDialog(QDialog):

    def __init__(self, connection, parent=None):
        super().__init__(parent)
        self.setWindowTitle('📁 Transfert de fichiers')
        self.setMinimumSize(640, 480)
        self._conn = connection
        self._upload_path = ''
        self._upload_id: int | None = None
        self._browse_id  = 0
        self._dl_id: int | None = None
        self._dl_path   = ''
        self._dl_parent = ''
        self._dl_entries: list = []

        # Connect signals
        connection.file_upload_ack.connect(self._on_up_ack)
        connection.file_upload_prog.connect(self._on_up_prog)
        connection.file_upload_done.connect(self._on_up_done)
        connection.file_upload_abort.connect(self._on_up_abort)
        connection.file_list_received.connect(self._on_file_list)
        connection.file_dl_info.connect(self._on_dl_info)
        connection.file_dl_prog.connect(self._on_dl_prog)
        connection.file_dl_done.connect(self._on_dl_done)
        connection.file_dl_abort.connect(self._on_dl_abort)

        self._build_ui()
        # Auto-browse home dir on host
        self._do_browse('')

    # ── Build UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        # ── Tab 1: Upload (client → host) ─────────────────────────────────
        up_w = QWidget()
        up_l = QVBoxLayout(up_w)
        up_l.setSpacing(10)

        btn_choose = QPushButton('📂  Choisir un fichier…')
        btn_choose.clicked.connect(self._choose_file)
        up_l.addWidget(btn_choose)

        self._up_lbl = QLabel('Aucun fichier sélectionné')
        self._up_lbl.setStyleSheet('color:#6c7086; font-size:12px;')
        up_l.addWidget(self._up_lbl)

        self._btn_send = QPushButton('📤  Envoyer vers le host')
        self._btn_send.setEnabled(False)
        self._btn_send.setStyleSheet(
            'background:#89b4fa; color:#1e1e2e; font-weight:bold;'
            'border-radius:6px; padding:8px;'
        )
        self._btn_send.clicked.connect(self._start_upload)
        up_l.addWidget(self._btn_send)

        self._up_bar = QProgressBar()
        self._up_bar.setVisible(False)
        up_l.addWidget(self._up_bar)

        self._up_status = QLabel('')
        self._up_status.setStyleSheet('color:#cdd6f4; font-size:12px;')
        up_l.addWidget(self._up_status)

        info = QLabel(
            '💡 Les fichiers envoyés arrivent dans le dossier\n'
            '   Desktop/EcranDistant  (ou ~/EcranDistant sur Linux/Mac)'
        )
        info.setStyleSheet('color:#6c7086; font-size:11px;')
        up_l.addWidget(info)
        up_l.addStretch()

        tabs.addTab(up_w, '📤 Envoyer')

        # ── Tab 2: Download (host → client) ───────────────────────────────
        dl_w = QWidget()
        dl_l = QVBoxLayout(dl_w)
        dl_l.setSpacing(6)

        path_row = QHBoxLayout()
        self._path_lbl = QLabel('…')
        self._path_lbl.setStyleSheet('color:#89b4fa; font-size:12px;')
        self._path_lbl.setWordWrap(True)
        btn_parent = QPushButton('↑')
        btn_parent.setFixedWidth(32)
        btn_parent.setToolTip('Dossier parent')
        btn_parent.clicked.connect(self._go_parent)
        btn_refresh = QPushButton('🔄')
        btn_refresh.setFixedWidth(32)
        btn_refresh.setToolTip('Actualiser')
        btn_refresh.clicked.connect(lambda: self._do_browse(self._dl_path))
        path_row.addWidget(self._path_lbl, 1)
        path_row.addWidget(btn_parent)
        path_row.addWidget(btn_refresh)
        dl_l.addLayout(path_row)

        self._file_list = QListWidget()
        self._file_list.setStyleSheet(
            'background:#1e1e2e; color:#cdd6f4; border:1px solid #313244;'
            'border-radius:6px; font-size:13px;'
        )
        self._file_list.itemClicked.connect(self._on_item_click)
        self._file_list.itemDoubleClicked.connect(self._on_item_dbl)
        dl_l.addWidget(self._file_list, 1)

        self._btn_dl = QPushButton('📥  Télécharger le fichier sélectionné')
        self._btn_dl.setEnabled(False)
        self._btn_dl.setStyleSheet(
            'background:#a6e3a1; color:#1e1e2e; font-weight:bold;'
            'border-radius:6px; padding:8px;'
        )
        self._btn_dl.clicked.connect(self._start_download)
        dl_l.addWidget(self._btn_dl)

        self._dl_bar = QProgressBar()
        self._dl_bar.setVisible(False)
        dl_l.addWidget(self._dl_bar)

        self._dl_status = QLabel('')
        self._dl_status.setStyleSheet('color:#cdd6f4; font-size:12px;')
        dl_l.addWidget(self._dl_status)

        tabs.addTab(dl_w, '📥 Télécharger')

    # ── Upload helpers ─────────────────────────────────────────────────────

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choisir un fichier à envoyer')
        if not path:
            return
        self._upload_path = path
        size = os.path.getsize(path)
        self._up_lbl.setText(
            f'{os.path.basename(path)}   ({self._fmt(size)})'
        )
        self._up_lbl.setStyleSheet('color:#cdd6f4; font-size:12px;')
        self._btn_send.setEnabled(True)
        self._up_status.setText('')
        self._up_bar.setVisible(False)

    def _start_upload(self):
        if not self._upload_path:
            return
        self._upload_id = int(time.time() * 1000) & 0xFFFF_FFFF
        self._btn_send.setEnabled(False)
        self._up_bar.setValue(0)
        self._up_bar.setVisible(True)
        self._up_status.setText('⏳ Envoi en cours…')
        self._conn.send_file(self._upload_id, self._upload_path)

    def _on_up_ack(self, tid: int, ok: bool, reason: str):
        if tid != self._upload_id:
            return
        if not ok:
            self._up_status.setText(f'❌ Refusé par le host : {reason}')
            self._up_bar.setVisible(False)
            self._btn_send.setEnabled(True)

    def _on_up_prog(self, tid: int, sent: int, total: int):
        if tid != self._upload_id:
            return
        self._up_bar.setValue(int(sent * 100 / total) if total else 0)
        self._up_status.setText(f'{self._fmt(sent)} / {self._fmt(total)}')

    def _on_up_done(self, tid: int):
        if tid != self._upload_id:
            return
        self._up_bar.setValue(100)
        self._up_status.setText('✅ Fichier envoyé avec succès !')
        self._upload_id = None

    def _on_up_abort(self, tid: int, reason: str):
        if tid != self._upload_id:
            return
        self._up_status.setText(f'❌ Erreur : {reason}')
        self._up_bar.setVisible(False)
        self._btn_send.setEnabled(bool(self._upload_path))
        self._upload_id = None

    # ── Download / browse helpers ──────────────────────────────────────────

    def _do_browse(self, path: str):
        self._browse_id = int(time.time() * 1000) & 0xFFFF_FFFF
        self._file_list.clear()
        self._btn_dl.setEnabled(False)
        self._path_lbl.setText('⏳ Chargement…')
        self._conn.browse_host(self._browse_id, path)

    def _go_parent(self):
        if self._dl_parent:
            self._do_browse(self._dl_parent)

    def _on_file_list(self, _tid: int, path: str, parent: str, entries: list):
        self._dl_path   = path
        self._dl_parent = parent
        self._dl_entries = entries
        self._path_lbl.setText(path or '/')
        self._file_list.clear()
        self._btn_dl.setEnabled(False)
        for e in entries:
            icon = '📁' if e['is_dir'] else '📄'
            size_s = f"   {self._fmt(e['size'])}" if not e['is_dir'] else ''
            item = QListWidgetItem(f"{icon}  {e['name']}{size_s}")
            item.setData(Qt.ItemDataRole.UserRole, e)
            self._file_list.addItem(item)

    def _on_item_click(self, item: QListWidgetItem):
        e = item.data(Qt.ItemDataRole.UserRole)
        self._btn_dl.setEnabled(bool(e and not e['is_dir']))

    def _on_item_dbl(self, item: QListWidgetItem):
        e = item.data(Qt.ItemDataRole.UserRole)
        if not e:
            return
        if e['is_dir']:
            self._do_browse(os.path.join(self._dl_path, e['name']))
        else:
            self._start_download()

    def _start_download(self):
        item = self._file_list.currentItem()
        if not item:
            return
        e = item.data(Qt.ItemDataRole.UserRole)
        if not e or e['is_dir']:
            return

        save_path, _ = QFileDialog.getSaveFileName(self, 'Enregistrer sous', e['name'])
        if not save_path:
            return

        self._dl_save_path = save_path
        self._dl_id = int(time.time() * 1000) & 0xFFFF_FFFF
        remote = os.path.join(self._dl_path, e['name'])

        self._btn_dl.setEnabled(False)
        self._dl_bar.setValue(0)
        self._dl_bar.setVisible(True)
        self._dl_status.setText('⏳ Téléchargement…')

        self._conn.request_download(self._dl_id, remote)

    def _on_dl_info(self, tid: int, name: str, size: int):
        if tid != self._dl_id:
            return
        self._dl_status.setText(f'⏳ {name}  ({self._fmt(size)})')
        self._dl_bar.setValue(0)

    def _on_dl_prog(self, tid: int, recv: int, total: int):
        if tid != self._dl_id:
            return
        self._dl_bar.setValue(int(recv * 100 / total) if total else 0)
        self._dl_status.setText(f'{self._fmt(recv)} / {self._fmt(total)}')

    def _on_dl_done(self, tid: int, _name: str):
        if tid != self._dl_id:
            return
        tmp_path = self._conn.get_download_path(tid)
        try:
            shutil.move(tmp_path, self._dl_save_path)
            self._dl_bar.setValue(100)
            self._dl_status.setText(f'✅ Enregistré : {self._dl_save_path}')
        except Exception as exc:
            self._dl_status.setText(f'❌ Erreur sauvegarde : {exc}')
        self._dl_id = None
        self._btn_dl.setEnabled(True)

    def _on_dl_abort(self, tid: int, reason: str):
        if tid != self._dl_id:
            return
        self._dl_status.setText(f'❌ {reason}')
        self._dl_bar.setVisible(False)
        self._btn_dl.setEnabled(True)
        self._dl_id = None

    # ── Util ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt(size: int) -> str:
        if size >= 1_000_000_000:
            return f'{size/1e9:.1f} Go'
        if size >= 1_000_000:
            return f'{size/1e6:.1f} Mo'
        if size >= 1_000:
            return f'{size/1e3:.1f} Ko'
        return f'{size} o'


class ViewerWindow(QMainWindow):
    def __init__(self, connection):
        super().__init__()
        self._conn = connection
        self.remote_w  = 1920
        self.remote_h  = 1080
        self._monitors: list = []
        self._current_pixmap: QPixmap | None = None
        self._frame_count = 0
        self._fps = 0
        self._ignore_clip = False   # prevent clipboard echo loop
        self._file_dialog: FileTransferDialog | None = None
        self._chat_dialog: ChatDialog | None = None

        self.setWindowTitle('EcranDistant')
        self.resize(1280, 740)
        self.setMinimumSize(640, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_toolbar()
        self._build_screen()

        # FPS counter
        self._fps_timer = QTimer(self)
        self._fps_timer.timeout.connect(self._refresh_status)
        self._fps_timer.start(1000)

        self.statusBar().showMessage('Connecting…')

    # ── Toolbar ────────────────────────────────────────────────────────────

    def _build_toolbar(self):
        tb: QToolBar = self.addToolBar('Controls')
        tb.setMovable(False)
        tb.setStyleSheet('QToolBar { spacing: 4px; }')

        # Monitor selector
        self._mon_label = QLabel(' 🖥  Monitor: ')
        tb.addWidget(self._mon_label)
        self._mon_combo = QComboBox()
        self._mon_combo.setMinimumWidth(110)
        self._mon_combo.setEnabled(False)
        self._mon_combo.currentIndexChanged.connect(self._on_monitor_change)
        tb.addWidget(self._mon_combo)
        tb.addSeparator()

        # Audio toggle
        self._audio_action = QAction('🔇 Audio', self)
        self._audio_action.setCheckable(True)
        self._audio_action.setChecked(False)
        self._audio_action.setToolTip('Toggle remote audio (requires sounddevice)')
        self._audio_action.toggled.connect(self._on_audio_toggle)
        tb.addAction(self._audio_action)
        tb.addSeparator()

        # Clipboard sync toggle
        self._clip_action = QAction('📋 Clipboard', self)
        self._clip_action.setCheckable(True)
        self._clip_action.setChecked(True)
        self._clip_action.setToolTip('Sync clipboard between client and host')
        self._clip_action.toggled.connect(self._on_clip_toggle)
        tb.addAction(self._clip_action)
        tb.addSeparator()

        # File transfer
        file_action = QAction('📁 Fichiers', self)
        file_action.setToolTip('Transfert de fichiers (style TeamViewer)')
        file_action.triggered.connect(self._open_file_transfer)
        tb.addAction(file_action)

        # Chat
        self._chat_action = QAction('💬 Chat', self)
        self._chat_action.setToolTip('Chat texte avec le host')
        self._chat_action.triggered.connect(self._open_chat)
        tb.addAction(self._chat_action)
        tb.addSeparator()

        # Fullscreen
        fs_action = QAction('⛶ Fullscreen', self)
        fs_action.setShortcut('F11')
        fs_action.setToolTip('Toggle fullscreen (F11)')
        fs_action.triggered.connect(self._toggle_fullscreen)
        tb.addAction(fs_action)
        tb.addSeparator()

        # Quality slider
        tb.addWidget(QLabel(' Quality: '))
        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setRange(10, 90)
        self._quality_slider.setValue(50)
        self._quality_slider.setFixedWidth(90)
        self._quality_slider.setToolTip('JPEG quality (lower = faster, higher = sharper)')
        self._quality_slider.valueChanged.connect(self._on_quality_change)
        tb.addWidget(self._quality_slider)
        tb.addSeparator()

        # FPS selector
        tb.addWidget(QLabel(' FPS: '))
        self._fps_combo = QComboBox()
        for v in (5, 10, 15, 20, 30):
            self._fps_combo.addItem(str(v), v)
        self._fps_combo.setCurrentIndex(3)   # default 20
        self._fps_combo.currentIndexChanged.connect(self._on_fps_change)
        tb.addWidget(self._fps_combo)

        # Spacer + stats label
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self._stats_label = QLabel('—')
        tb.addWidget(self._stats_label)
        tb.addWidget(QLabel(' '))

    # ── Screen area ────────────────────────────────────────────────────────

    def _build_screen(self):
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet('background-color: #111;')
        self._label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label.setMouseTracking(True)
        self._label.installEventFilter(self)
        self.setCentralWidget(self._label)
        self.setMouseTracking(True)

    # ── Slots — connection signals ─────────────────────────────────────────

    @pyqtSlot(int, int, list, bool)
    def on_connected(self, w: int, h: int, monitors: list, has_audio: bool):
        self.remote_w = w
        self.remote_h = h
        self._monitors = monitors

        # Populate monitor combo
        self._mon_combo.blockSignals(True)
        self._mon_combo.clear()
        for m in monitors:
            self._mon_combo.addItem(
                f"{m['name']} ({m['width']}×{m['height']})", m['index']
            )
        self._mon_combo.setEnabled(len(monitors) > 1)
        self._mon_combo.blockSignals(False)

        # Bouton audio toujours actif — le host gère si l'audio est dispo
        self._audio_action.setEnabled(True)
        if not has_audio:
            self._audio_action.setToolTip('Audio non disponible sur le host (sounddevice manquant?)')
        else:
            self._audio_action.setToolTip('Activer/désactiver le son du host')
            # Active le son automatiquement dès la connexion
            self._audio_action.setChecked(True)

        self._refresh_status()

        # Clipboard change monitoring (Qt)
        if _QT_CLIP:
            QApplication.clipboard().dataChanged.connect(self._on_local_clip_change)

    @pyqtSlot(bytes, int, int)
    def on_frame(self, jpeg: bytes, w: int, h: int):
        self.remote_w = w
        self.remote_h = h
        img = QImage.fromData(jpeg, 'JPEG')
        self._current_pixmap = QPixmap.fromImage(img)
        self._render()
        self._frame_count += 1

    @pyqtSlot(int, int, int)
    def on_monitor_changed(self, w: int, h: int, index: int):
        self.remote_w = w
        self.remote_h = h
        self._refresh_status()

    @pyqtSlot(str)
    def on_clipboard_received(self, text: str):
        """Host sent clipboard → set ours (briefly suppress echo)."""
        if not self._clip_action.isChecked() or not text or not _QT_CLIP:
            return
        self._ignore_clip = True
        QApplication.clipboard().setText(text)
        QTimer.singleShot(200, self._unignore_clip)

    def _unignore_clip(self):
        self._ignore_clip = False

    # ── Toolbar callbacks ──────────────────────────────────────────────────

    def _on_monitor_change(self, idx: int):
        if idx < 0 or idx >= len(self._monitors):
            return
        self._conn.select_monitor(self._monitors[idx]['index'])

    def _on_audio_toggle(self, checked: bool):
        self._audio_action.setText('🔊 Audio' if checked else '🔇 Audio')
        self._conn.set_audio_enabled(checked)

    def _on_clip_toggle(self, checked: bool):
        pass   # handled in _on_local_clip_change and on_clipboard_received

    def _on_quality_change(self, value: int):
        self._conn.set_quality(value)

    def _on_fps_change(self, _idx: int):
        fps = self._fps_combo.currentData()
        if fps:
            self._conn.set_fps(fps)

    def _open_file_transfer(self):
        if self._file_dialog is None or not self._file_dialog.isVisible():
            self._file_dialog = FileTransferDialog(self._conn, self)
        self._file_dialog.show()
        self._file_dialog.raise_()

    def _open_chat(self):
        if self._chat_dialog is None:
            self._chat_dialog = ChatDialog(self._conn, self)
            # Connect to signal to flash button on new message
            self._conn.chat_received.connect(self._on_chat_notification)
        self._chat_dialog.show()
        self._chat_dialog.raise_()

    def _on_chat_notification(self, _text, sender, _ts):
        """Flash the Chat button when a message arrives from host."""
        if sender == 'host' and (self._chat_dialog is None
                                 or not self._chat_dialog.isVisible()):
            self._chat_action.setText('💬 Chat ●')
            QTimer.singleShot(3000, lambda: self._chat_action.setText('💬 Chat'))

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _on_local_clip_change(self):
        """Local clipboard changed → send to host."""
        if self._ignore_clip or not self._clip_action.isChecked():
            return
        text = QApplication.clipboard().text()
        if text:
            self._conn.send_clipboard(text)

    # ── Rendering ──────────────────────────────────────────────────────────

    def _render(self):
        if self._current_pixmap is None:
            return
        self._label.setPixmap(
            self._current_pixmap.scaled(
                self._label.width(),
                self._label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()

    # ── Status bar ─────────────────────────────────────────────────────────

    def _refresh_status(self):
        self._fps = self._frame_count
        self._frame_count = 0
        txt = f'Remote: {self.remote_w}×{self.remote_h}   FPS: {self._fps}'
        self.statusBar().showMessage(txt)
        self._stats_label.setText(f'{self._fps} FPS')

    # ── Coordinate mapping ─────────────────────────────────────────────────

    def _to_remote(self, lx: float, ly: float) -> tuple[int, int]:
        lw, lh = self._label.width(), self._label.height()
        rw, rh = self.remote_w, self.remote_h
        if rw * lh > rh * lw:
            rendered_w, rendered_h = lw, lw * rh / rw
        else:
            rendered_w, rendered_h = lh * rw / rh, lh
        ox = (lw - rendered_w) / 2
        oy = (lh - rendered_h) / 2
        rx = (lx - ox) * rw / rendered_w
        ry = (ly - oy) * rh / rendered_h
        return int(max(0, min(rx, rw - 1))), int(max(0, min(ry, rh - 1)))

    # ── Mouse event filter ─────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is not self._label:
            return super().eventFilter(obj, event)
        t = event.type()
        if t == QEvent.Type.MouseMove:
            x, y = self._to_remote(event.position().x(), event.position().y())
            self._conn.send_mouse('move', x, y)
        elif t == QEvent.Type.MouseButtonPress:
            x, y = self._to_remote(event.position().x(), event.position().y())
            self._conn.send_mouse('click', x, y, button=self._btn(event.button()), pressed=True)
            self._label.setFocus()
        elif t == QEvent.Type.MouseButtonRelease:
            x, y = self._to_remote(event.position().x(), event.position().y())
            self._conn.send_mouse('click', x, y, button=self._btn(event.button()), pressed=False)
        elif t == QEvent.Type.Wheel:
            x, y = self._to_remote(event.position().x(), event.position().y())
            self._conn.send_mouse('scroll', x, y,
                                  dx=event.angleDelta().x() // 120,
                                  dy=event.angleDelta().y() // 120)
        return super().eventFilter(obj, event)

    # ── Keyboard ───────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        # F11 = fullscreen (handled by QAction shortcut)
        key = self._resolve_key(event)
        if key:
            self._conn.send_key('press', key)

    def keyReleaseEvent(self, event: QKeyEvent):
        key = self._resolve_key(event)
        if key:
            self._conn.send_key('release', key)

    def _resolve_key(self, event: QKeyEvent) -> str:
        qt_key = Qt.Key(event.key())
        if qt_key in _QT_KEY_MAP:
            return _QT_KEY_MAP[qt_key]
        text = event.text()
        if text and text.isprintable():
            return text
        return ''

    @staticmethod
    def _btn(button) -> str:
        if button == Qt.MouseButton.RightButton:  return 'right'
        if button == Qt.MouseButton.MiddleButton: return 'middle'
        return 'left'

    # ── Close ──────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent):
        self._conn.stop()
        super().closeEvent(event)
