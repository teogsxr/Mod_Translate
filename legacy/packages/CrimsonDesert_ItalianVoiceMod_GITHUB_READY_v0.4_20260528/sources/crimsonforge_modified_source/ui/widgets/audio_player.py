"""Enterprise audio playback widget with full transport controls.

Features:
- Play/Pause/Stop with keyboard shortcuts (Space, S)
- Position slider with click-to-seek
- Volume slider with mute toggle
- Loop toggle for repeat playback
- Time display: current / total duration
- Format info label (sample rate, channels, codec)
- Reusable for both standalone audio and video control strip
"""

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QSlider,
    QToolButton, QSizePolicy,
)
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtGui import QKeySequence, QShortcut


class AudioPlayerWidget(QWidget):
    """Audio player with play/pause/stop, seek, volume, and loop controls."""

    playback_started = Signal()
    playback_stopped = Signal()

    def __init__(self, parent=None, standalone: bool = True):
        """
        Args:
            parent: Parent widget.
            standalone: If True, creates its own QMediaPlayer + QAudioOutput.
                       If False, expects external player/output via set_player().
        """
        super().__init__(parent)
        self._standalone = standalone
        self._looping = False

        if standalone:
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
        else:
            self._player = None
            self._audio_output = None

        self._setup_ui()

        if self._player:
            self._connect_player_signals()

    def set_player(self, player: QMediaPlayer, audio_output: QAudioOutput) -> None:
        """Attach an external media player (used for video controls)."""
        self._player = player
        self._audio_output = audio_output
        self._connect_player_signals()

    def _connect_player_signals(self):
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        # Transport controls row
        transport = QHBoxLayout()
        transport.setSpacing(4)

        self._play_btn = QPushButton("Play")
        self._play_btn.setFixedWidth(64)
        self._play_btn.setToolTip("Play / Pause (Space)")
        self._play_btn.clicked.connect(self._toggle_play)
        transport.addWidget(self._play_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setFixedWidth(48)
        self._stop_btn.setToolTip("Stop (S)")
        self._stop_btn.clicked.connect(self._stop)
        transport.addWidget(self._stop_btn)

        # Position slider
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderMoved.connect(self._seek)
        self._slider.setToolTip("Seek position")
        transport.addWidget(self._slider, 1)

        # Time label
        self._time_label = QLabel("0:00 / 0:00")
        self._time_label.setMinimumWidth(90)
        self._time_label.setStyleSheet("font-size: 11px; font-family: monospace; color: #cdd6f4;")
        transport.addWidget(self._time_label)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet("color: #45475a; padding: 0 2px;")
        transport.addWidget(sep)

        # Volume icon + slider
        self._mute_btn = QToolButton()
        self._mute_btn.setText("Vol")
        self._mute_btn.setFixedWidth(32)
        self._mute_btn.setToolTip("Mute / Unmute (M)")
        self._mute_btn.setCheckable(True)
        self._mute_btn.clicked.connect(self._toggle_mute)
        transport.addWidget(self._mute_btn)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(80)
        self._volume_slider.setToolTip("Volume")
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        transport.addWidget(self._volume_slider)

        # Loop toggle
        self._loop_btn = QToolButton()
        self._loop_btn.setText("Loop")
        self._loop_btn.setFixedWidth(40)
        self._loop_btn.setToolTip("Toggle loop playback (L)")
        self._loop_btn.setCheckable(True)
        self._loop_btn.clicked.connect(self._toggle_loop)
        transport.addWidget(self._loop_btn)

        layout.addLayout(transport)

        # Info row
        self._info_label = QLabel("")
        self._info_label.setStyleSheet("font-size: 10px; color: #6c7086; padding: 0 4px;")
        layout.addWidget(self._info_label)

        # Set initial volume
        if self._audio_output:
            self._audio_output.setVolume(0.8)

        # Keyboard shortcuts
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        space_sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        space_sc.activated.connect(self._toggle_play)
        s_sc = QShortcut(QKeySequence(Qt.Key_S), self)
        s_sc.activated.connect(self._stop)
        m_sc = QShortcut(QKeySequence(Qt.Key_M), self)
        m_sc.activated.connect(self._toggle_mute)
        l_sc = QShortcut(QKeySequence(Qt.Key_L), self)
        l_sc.activated.connect(self._toggle_loop)

    def load_file(self, path: str) -> None:
        if not self._player:
            return
        self._player.setSource(QUrl.fromLocalFile(path))
        self._play_btn.setText("Play")
        self._update_info(path)

    def _update_info(self, path: str) -> None:
        """Show file format info."""
        import os
        basename = os.path.basename(path)
        ext = os.path.splitext(path)[1].upper().lstrip(".")
        try:
            size = os.path.getsize(path)
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / 1024 / 1024:.1f} MB"
        except OSError:
            size_str = ""
        info_parts = [ext]
        if size_str:
            info_parts.append(size_str)
        loop_status = "Loop: ON" if self._looping else "Loop: OFF"
        info_parts.append(loop_status)
        self._info_label.setText("  |  ".join(info_parts))

    def _toggle_play(self):
        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()
            self.playback_started.emit()

    def _stop(self):
        if not self._player:
            return
        self._player.stop()
        self.playback_stopped.emit()

    def _seek(self, position):
        if self._player:
            self._player.setPosition(position)

    def _toggle_mute(self):
        if not self._audio_output:
            return
        muted = self._mute_btn.isChecked()
        self._audio_output.setMuted(muted)
        self._mute_btn.setText("Mut" if muted else "Vol")
        self._volume_slider.setEnabled(not muted)

    def _on_volume_changed(self, value):
        if self._audio_output:
            self._audio_output.setVolume(value / 100.0)

    def _toggle_loop(self):
        self._looping = self._loop_btn.isChecked()
        if self._player:
            if self._looping:
                self._player.setLoops(QMediaPlayer.Infinite)
            else:
                self._player.setLoops(1)
        self._loop_btn.setStyleSheet(
            "background-color: #89b4fa; color: #1e1e2e;" if self._looping else ""
        )
        # Update info label loop status
        current = self._info_label.text()
        if "Loop:" in current:
            parts = current.split("  |  ")
            parts = [p for p in parts if not p.strip().startswith("Loop:")]
            parts.append("Loop: ON" if self._looping else "Loop: OFF")
            self._info_label.setText("  |  ".join(parts))

    def _on_position_changed(self, position):
        if not self._slider.isSliderDown():
            self._slider.setValue(position)
        self._update_time_label(position, self._player.duration() if self._player else 0)

    def _on_duration_changed(self, duration):
        self._slider.setRange(0, duration)
        self._update_time_label(self._player.position() if self._player else 0, duration)

    def _on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self._play_btn.setText("Pause")
        elif state == QMediaPlayer.PausedState:
            self._play_btn.setText("Play")
        else:
            self._play_btn.setText("Play")
            self.playback_stopped.emit()

    def _on_media_status(self, status):
        """Handle end-of-media for looping."""
        if status == QMediaPlayer.EndOfMedia and self._looping:
            if self._player:
                self._player.setPosition(0)
                self._player.play()

    def _update_time_label(self, pos_ms, dur_ms):
        def fmt(ms):
            total_s = max(0, ms // 1000)
            m, s = divmod(total_s, 60)
            if m >= 60:
                h, m = divmod(m, 60)
                return f"{h}:{m:02d}:{s:02d}"
            return f"{m}:{s:02d}"
        self._time_label.setText(f"{fmt(pos_ms)} / {fmt(dur_ms)}")

    def set_volume(self, value: int) -> None:
        """Set volume (0-100)."""
        self._volume_slider.setValue(value)

    def cleanup(self):
        if self._player:
            self._player.stop()
            self._player.setSource(QUrl())
        self._slider.setValue(0)
        self._slider.setRange(0, 0)
        self._time_label.setText("0:00 / 0:00")
        self._info_label.setText("")
        self._play_btn.setText("Play")
