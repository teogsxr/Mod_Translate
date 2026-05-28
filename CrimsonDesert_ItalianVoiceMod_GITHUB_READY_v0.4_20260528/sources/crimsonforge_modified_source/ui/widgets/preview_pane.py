"""Multi-format file preview pane.

Displays:
- Images: PNG, JPG, BMP, TGA, DDS, WebP, GIF (scaled to fit)
- Audio: WAV, OGG, MP3 with play/pause/stop/seek/volume/loop controls
- Audio: WEM, BNK (Wwise) with auto-install vgmstream transcoding
- Video: MP4, WebM, AVI with full player controls
- HTML/THTML: Real rendered preview via QWebEngineView
- CSS: Rendered preview (wrapped in HTML) via QWebEngineView
- Fonts: TTF/OTF sample text preview with loaded font
- Text: Syntax-highlighted read-only view (XML, JSON, paloc, plain)
- Binary: Hex viewer with ASCII sidebar
"""

import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QStackedWidget,
    QPlainTextEdit, QScrollArea, QPushButton,
)
from PySide6.QtGui import QPixmap, QFont, QImage
from PySide6.QtCore import Qt, QUrl

from core.file_detector import detect_file_type
from ui.widgets.audio_player import AudioPlayerWidget
from utils.logger import get_logger
from utils.platform_utils import format_file_size

logger = get_logger("ui.preview_pane")

IDX_EMPTY = 0
IDX_IMAGE = 1
IDX_TEXT = 2
IDX_HEX = 3
IDX_FONT = 4
IDX_AUDIO = 5
IDX_VIDEO = 6
IDX_WEB = 7
IDX_MESH = 8

_WEB_ENGINE_LOADED = False
_QWebEngineView = None
_QAudioOutput = None
_QMediaPlayer = None
_QVideoWidget = None


def _prepare_mesh_data_async(
    data: bytes, path: str, pane_state: dict,
) -> dict:
    """Worker-safe mesh preparation.

    Pure-Python pipeline: parses the mesh, flattens it for the
    viewer, resolves textures via the cached PAMT index, and builds
    the GPU texture payload. Returns a dict the UI thread can feed
    into :meth:`PreviewPane._render_prepared_mesh` to do the GL upload.

    No Qt or OpenGL calls are made here — safe to run on a
    ``FunctionWorker`` thread. ``pane_state`` is a snapshot of the
    pane's vfs / vfs_path (so the worker doesn't race with the next
    click changing those on the live PreviewPane instance).
    """
    try:
        from core.mesh_parser import (
            parse_mesh,
            _flatten_parsed_mesh_for_preview,
            _build_pac_preview_mesh,
        )
        ext = os.path.splitext(path.lower())[1]
        full_mesh = None
        vfs = pane_state.get("_active_vfs")
        vfs_path = pane_state.get("_active_vfs_path")

        if ext == ".pac":
            preview_mesh = _build_pac_preview_mesh(
                data, os.path.basename(path),
            )
            # ── Texture path needs the full submesh tree ──
            # ``_build_pac_preview_mesh`` produces a fast flat triangle
            # soup for the GL viewer but no per-submesh material
            # binding. The texture resolver needs the real submesh
            # records (each submesh has its own pac_xml material entry
            # that maps to a DDS path), so when the pane was opened
            # with a VFS context we ALSO run the full ``parse_mesh``
            # pass to populate ``full_mesh``. Without this the texture
            # pipeline below silently no-ops on every PAC and the user
            # sees an untextured mesh — exactly the "no texture, only
            # Loading mesh…" report.
            if vfs is not None and vfs_path:
                try:
                    full_mesh = parse_mesh(data, os.path.basename(path))
                except Exception:
                    # Non-fatal — preview still renders monochrome
                    # without textures if the full parse blows up.
                    full_mesh = None
        else:
            full_mesh = parse_mesh(data, os.path.basename(path))
            preview_mesh = _flatten_parsed_mesh_for_preview(full_mesh)

        info_text = (
            f"{preview_mesh.total_vertices:,} verts | "
            f"{preview_mesh.total_faces:,} faces | "
            f"{preview_mesh.submesh_count} submesh(es)"
        )

        # Texture pipeline (cached pamt_index, near-zero cost on
        # the second click — see core.mesh_texture_service).
        face_colors: list = []
        texture_payload = None
        if vfs is not None and vfs_path and full_mesh is not None:
            try:
                from core.mesh_texture_service import (
                    build_gpu_texture_payload,
                    compute_mesh_texture_report,
                )
                report = compute_mesh_texture_report(
                    vfs, vfs_path, full_mesh,
                )
                if report.any_textured:
                    for sm, entry in zip(
                        full_mesh.submeshes, report.submeshes,
                    ):
                        if entry is None:
                            face_colors.extend(
                                [(180, 180, 180, 255)] * len(sm.faces),
                            )
                        else:
                            face_colors.extend(entry.face_colors)
                    texture_payload = build_gpu_texture_payload(
                        full_mesh, report,
                    )
            except Exception:
                # Non-fatal — preview still renders monochrome.
                pass

        return {
            "preview_mesh": preview_mesh,
            "full_mesh": full_mesh,
            "info_text": info_text,
            "face_colors": face_colors,
            "texture_payload": texture_payload,
            "raw_data": data,
            "path": path,
        }
    except Exception as e:
        return {"error": str(e)}


def _ensure_multimedia():
    global _QAudioOutput, _QMediaPlayer, _QVideoWidget
    if _QMediaPlayer is None:
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PySide6.QtMultimediaWidgets import QVideoWidget
        _QMediaPlayer = QMediaPlayer
        _QAudioOutput = QAudioOutput
        _QVideoWidget = QVideoWidget


def _ensure_web_engine():
    global _WEB_ENGINE_LOADED, _QWebEngineView
    if not _WEB_ENGINE_LOADED:
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            _QWebEngineView = QWebEngineView
        except ImportError:
            _QWebEngineView = None
        _WEB_ENGINE_LOADED = True


class PreviewPane(QWidget):
    """Multi-format file preview widget with audio/video/HTML rendering."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path = ""
        self._vgmstream_installing = False
        # VFS hints the Explorer passes through ``preview_file`` so the
        # mesh preview can discover paired textures. Left unset when a
        # file is previewed directly from the filesystem.
        self._active_vfs = None
        self._active_vfs_path = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._info_label = QLabel("Select a file to preview")
        self._info_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._info_label)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack, 1)

        # IDX_EMPTY = 0
        self._empty_widget = QWidget()
        empty_layout = QVBoxLayout(self._empty_widget)
        empty_layout.setAlignment(Qt.AlignCenter)
        self._empty_label = QLabel("No preview available")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("font-size: 13px; padding: 16px;")
        empty_layout.addWidget(self._empty_label)
        self._stack.addWidget(self._empty_widget)

        # IDX_IMAGE = 1
        self._image_scroll = QScrollArea()
        self._image_scroll.setWidgetResizable(True)
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_scroll.setWidget(self._image_label)
        self._stack.addWidget(self._image_scroll)

        # IDX_TEXT = 2
        self._text_edit = QPlainTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Courier New", 10))
        self._text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._stack.addWidget(self._text_edit)

        # IDX_HEX = 3
        self._hex_edit = QPlainTextEdit()
        self._hex_edit.setReadOnly(True)
        self._hex_edit.setFont(QFont("Courier New", 9))
        self._hex_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._stack.addWidget(self._hex_edit)

        # IDX_FONT = 4
        self._font_label = QLabel()
        self._font_label.setAlignment(Qt.AlignCenter)
        self._font_label.setWordWrap(True)
        self._stack.addWidget(self._font_label)

        # IDX_AUDIO = 5
        self._audio_container = QWidget()
        audio_layout = QVBoxLayout(self._audio_container)
        audio_layout.setContentsMargins(8, 8, 8, 8)
        self._audio_info = QLabel("")
        self._audio_info.setAlignment(Qt.AlignCenter)
        self._audio_info.setStyleSheet("font-size: 48px; padding: 20px;")
        self._audio_info.setText("Audio")
        audio_layout.addWidget(self._audio_info, 1)
        self._audio_player = AudioPlayerWidget(standalone=True)
        audio_layout.addWidget(self._audio_player)
        self._stack.addWidget(self._audio_container)

        # IDX_VIDEO = 6
        _ensure_multimedia()
        self._video_container = QWidget()
        video_layout = QVBoxLayout(self._video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self._video_widget = _QVideoWidget()
        video_layout.addWidget(self._video_widget, 1)
        self._video_player = _QMediaPlayer()
        self._video_audio = _QAudioOutput()
        self._video_player.setAudioOutput(self._video_audio)
        self._video_player.setVideoOutput(self._video_widget)
        # Video controls connected to video player
        self._video_controls = AudioPlayerWidget(standalone=False)
        self._video_controls.set_player(self._video_player, self._video_audio)
        video_layout.addWidget(self._video_controls)
        self._stack.addWidget(self._video_container)

        # IDX_WEB = 7
        self._web_view = None
        _ensure_web_engine()
        if _QWebEngineView is not None:
            self._web_view = _QWebEngineView()
            self._stack.addWidget(self._web_view)
        else:
            self._web_placeholder = QLabel("Web preview unavailable in this build")
            self._web_placeholder.setAlignment(Qt.AlignCenter)
            self._web_placeholder.setWordWrap(True)
            self._web_placeholder.setStyleSheet("font-size: 13px; padding: 16px; color: #6c7086;")
            self._stack.addWidget(self._web_placeholder)

        # IDX_MESH = 8
        self._mesh_viewer = None
        self._mesh_placeholder = None
        try:
            from ui.widgets.mesh_viewer import MeshViewer
            self._mesh_viewer = MeshViewer()
            logger.info("Mesh preview backend: %s", type(self._mesh_viewer).__name__)
            self._stack.addWidget(self._mesh_viewer)
        except Exception as exc:
            logger.exception("Mesh preview backend unavailable: %s", exc)
            self._mesh_placeholder = QLabel("Interactive mesh preview unavailable in this build")
            self._mesh_placeholder.setAlignment(Qt.AlignCenter)
            self._mesh_placeholder.setWordWrap(True)
            self._mesh_placeholder.setStyleSheet("font-size: 13px; padding: 16px; color: #f9e2af;")
            self._stack.addWidget(self._mesh_placeholder)

        self._stack.setCurrentIndex(IDX_EMPTY)

    def preview_file(self, path: str, *, vfs=None, vfs_path: str = "") -> None:
        """Preview a file based on its detected type.

        ``vfs`` and ``vfs_path`` are optional hints the caller can pass when
        the file being previewed originated from a game archive. Currently
        only the mesh preview uses them, to discover and apply the paired
        diffuse texture (``core.mesh_texture_service``). Leaving them
        unset keeps the pure-filesystem preview path working unchanged.
        """
        self._stop_media()

        if not os.path.isfile(path):
            self._show_empty("File not found")
            return

        self._current_path = path
        self._active_vfs = vfs
        self._active_vfs_path = vfs_path
        size = os.path.getsize(path)
        file_info = detect_file_type(path)
        self._info_label.setText(
            f"{os.path.basename(path)}  |  {file_info.description}  |  {format_file_size(size)}"
        )

        ext = file_info.extension.lower()

        if ext in (".pam", ".pamlod", ".pac"):
            self._show_mesh_info(path)
        elif ext == ".hkx":
            self._show_havok_info(path)
        elif ext == ".nav":
            self._show_nav_info(path)
        elif ext == ".pab":
            self._show_skeleton_info(path)
        elif ext == ".paa" and ext != ".paa_metabin":
            self._show_animation_info(path)
        elif ext == ".dds":
            self._show_image(path)
            # Append DDS-specific info
            try:
                from core.dds_reader import read_dds_info
                with open(path, "rb") as f:
                    dds_info = read_dds_info(f.read())
                self._info_label.setText(
                    self._info_label.text() +
                    f"  |  {dds_info.format}  |  {dds_info.width}x{dds_info.height}  |  mips:{dds_info.mip_count}"
                )
            except Exception:
                pass
        elif file_info.category == "image":
            self._show_image(path)
        elif file_info.category == "audio":
            self._show_audio(path)
        elif file_info.category == "video":
            self._show_video(path)
        elif file_info.category == "font":
            self._show_font(path)
        elif ext in (".html", ".thtml", ".css"):
            self._show_web(path, ext)
        elif ext == ".pabgb":
            self._show_pabgb_table(path)
        elif file_info.category == "text" or file_info.can_edit:
            self._show_text(path)
        else:
            self._show_hex(path)

    def _stop_media(self) -> None:
        self._audio_player.cleanup()
        self._video_player.stop()
        self._video_player.setSource(QUrl())

    def _show_empty(self, message: str = "No preview available") -> None:
        self._empty_label.setText(message)
        self._stack.setCurrentIndex(IDX_EMPTY)

    def _show_havok_info(self, path: str) -> None:
        """Show Havok HKX summary using the Layer 1-5 parser stack.

        Falls back to the legacy ``core.havok_parser.get_hkx_summary``
        if the new TAG0 pipeline can't load the file — the two are
        complementary (legacy scans heuristically, new stack fails
        strict) so every file still renders something useful.
        """
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as exc:
            self._show_empty(f"HKX read error: {exc}")
            return

        lines: list[str] = []

        # Primary path — HkxDocument summary + type registry +
        # instance walker + physics risk verdict.
        try:
            from core.havok_tag0_document import HkxDocument
            from core.havok_parser import assess_mesh_edit_risk

            hkx = HkxDocument.safe_load(data)
            if hkx is not None:
                lines.append(hkx.summary())
                lines.append("")

                risk = assess_mesh_edit_risk(data)
                if risk.severity != "none":
                    lines.append(f"Physics edit risk: {risk.severity.upper()}")
                    if risk.driving_systems:
                        lines.append(f"  systems: {', '.join(risk.driving_systems)}")
                    for r in risk.reasons:
                        lines.append(f"  - {r}")
                else:
                    lines.append("Physics edit risk: none (skeleton / animation only)")
                lines.append("")

                classes = hkx.registry.types
                if classes:
                    lines.append(f"Classes ({len(classes)}):")
                    for t in classes[:20]:
                        lines.append(f"  {t.qualified_name()}")
                    if len(classes) > 20:
                        lines.append(f"  ... ({len(classes) - 20} more)")
                    lines.append("")

                instances = list(hkx.iter_instances())
                if instances:
                    lines.append(f"Instances ({len(instances)}):")
                    for inst in instances[:15]:
                        lines.append(
                            f"  [{inst.item.index:4d}] {inst.class_name}  "
                            f"@0x{inst.offset:06X}  payload={len(inst.payload)}B  "
                            f"flags=0x{inst.flags:02X}  count={inst.item.count}"
                        )
                    if len(instances) > 15:
                        lines.append(f"  ... ({len(instances) - 15} more)")
                self._text_edit.setPlainText("\n".join(lines))
                self._stack.setCurrentIndex(IDX_TEXT)
                return
        except Exception as exc:
            logger.debug("HkxDocument preview failed, falling back: %s", exc)

        # Fallback: legacy string-scan summary.
        try:
            from core.havok_parser import get_hkx_summary
            summary = get_hkx_summary(data)
            self._text_edit.setPlainText(summary)
            self._stack.setCurrentIndex(IDX_TEXT)
        except Exception as exc:
            self._show_empty(f"HKX parse error: {exc}")

    def _show_nav_info(self, path: str) -> None:
        """Show navigation mesh info."""
        try:
            from core.navmesh_parser import get_nav_summary
            with open(path, "rb") as f:
                summary = get_nav_summary(f.read())
            self._text_edit.setPlainText(summary)
            self._stack.setCurrentIndex(IDX_TEXT)
        except Exception as e:
            self._show_empty(f"NAV parse error: {e}")

    def _show_skeleton_info(self, path: str) -> None:
        """Show PAB skeleton bone hierarchy."""
        try:
            from core.skeleton_parser import parse_pab
            with open(path, "rb") as f:
                data = f.read()
            if data[:4] != b"PAR ":
                self._show_empty("Not a valid PAB skeleton file")
                return
            skel = parse_pab(data, os.path.basename(path))
            lines = [
                f"=== PAB Skeleton ===",
                f"Bones: {len(skel.bones)}",
                f"",
            ]
            for b in skel.bones:
                parent = skel.bones[b.parent_index].name if 0 <= b.parent_index < len(skel.bones) else "ROOT"
                indent = "  " * min(4, b.index // 10)
                lines.append(f"{indent}[{b.index:3d}] {b.name} -> {parent}")
            self._text_edit.setPlainText("\n".join(lines))
            self._stack.setCurrentIndex(IDX_TEXT)
        except Exception as e:
            self._show_empty(f"PAB parse error: {e}")

    def _show_animation_info(self, path: str) -> None:
        """Show PAA animation info with sparse-rig caveats and an export hint."""
        try:
            from core.animation_parser import parse_paa
            with open(path, "rb") as f:
                data = f.read()
            if data[:4] != b"PAR ":
                self._show_empty("Not a valid PAA animation file")
                return
            anim = parse_paa(data, os.path.basename(path))

            total_quats = len(anim.raw_quaternions)
            bones = anim.bone_count
            frames = anim.frame_count
            expected = max(1, bones) * max(1, frames)

            lines = [
                f"=== PAA Animation ===",
                f"Duration:     {anim.duration:.2f}s",
                f"Frames:       {frames}",
                f"Bones (rig):  {bones}",
                f"Quaternions:  {total_quats}",
            ]
            # LOD animations carry a rig-wide bone count in the header
            # but store only the subset of bones the animation actually
            # touches. Surface this explicitly so the user doesn't get
            # confused when 218 bones / 6 quats shows up on an LOD file.
            if total_quats < expected and bones > 0 and frames == 1:
                effective_bones = total_quats // frames if frames else total_quats
                lines.append(
                    f"Animated:     {effective_bones} bone(s) out of {bones} "
                    f"(likely a sparse LOD pose)"
                )
            elif total_quats < expected:
                lines.append(
                    f"Warning:      quat count {total_quats} is less than "
                    f"bones x frames ({expected}); later frames may be truncated"
                )
            lines.append("")
            if anim.keyframes:
                lines.append("First frame bone rotations:")
                kf = anim.keyframes[0]
                for i, (qx, qy, qz, qw) in enumerate(kf.bone_rotations[:10]):
                    lines.append(f"  Bone {i}: ({qx:.4f}, {qy:.4f}, {qz:.4f}, {qw:.4f})")
                if len(kf.bone_rotations) > 10:
                    lines.append(f"  ... and {len(kf.bone_rotations) - 10} more")
                lines.append("")

            lines.append("Export: right-click the file in the list for \"Export as FBX\".")

            self._text_edit.setPlainText("\n".join(lines))
            self._stack.setCurrentIndex(IDX_TEXT)
        except Exception as e:
            self._show_empty(f"PAA parse error: {e}")

    def _show_image(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".dds":
            dds_error = self._get_dds_preview_error(path)
            if dds_error:
                self._show_empty(dds_error)
                return

        pixmap = QPixmap(path)

        if pixmap.isNull() and ext == ".dds":
            pixmap = self._decode_dds_native(path)

        if pixmap.isNull() and ext in (".dds", ".tga"):
            pixmap = self._convert_image_with_pillow(path)

        if pixmap is None or pixmap.isNull():
            self._show_empty(f"Cannot load image: {os.path.basename(path)}")
            return

        pixmap = self._crop_transparent_preview_bounds(pixmap)

        max_w = max(self._image_scroll.width() - 20, 200)
        max_h = max(self._image_scroll.height() - 20, 200)
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image_label.setPixmap(pixmap)
        self._info_label.setText(
            self._info_label.text() + f"  |  {pixmap.width()}x{pixmap.height()}"
        )
        self._stack.setCurrentIndex(IDX_IMAGE)

    def _get_dds_preview_error(self, path: str) -> str:
        """Return a user-facing DDS preview error, or an empty string when the file is safe to decode."""
        try:
            from core.dds_reader import validate_dds_payload_size

            with open(path, "rb") as f:
                validate_dds_payload_size(f.read())
            return ""
        except Exception as exc:
            return f"DDS preview unavailable: {exc}"

    def _compute_preview_texture_data(self, data: bytes, path: str,
                                       parsed_mesh=None):
        """Resolve textures for the mesh and return GPU-ready payload + face colours.

        The OpenGL viewer consumes the ``GpuTexturePayload`` for true
        per-pixel texturing (interpolated UVs, nearest-DDS-texel
        sampling with mipmaps). The software viewer falls back to the
        ``face_colors`` list because it can't do per-pixel shading at
        interactive rates in pure Python.

        Returns ``(face_colors, texture_payload)`` — either may be
        empty / None when no diffuse textures resolved.

        ``parsed_mesh``: pre-parsed mesh from the caller. When ``None``
        we fall back to parsing here, but that path was responsible
        for the duplicate-parse cost (a 27k-vert PAMLOD got parsed
        once by ``build_preview_mesh`` and AGAIN here, ~0.5 s each).
        Callers that already hold a parsed mesh should pass it in to
        eliminate the second parse.
        """
        if self._active_vfs is None or not self._active_vfs_path:
            return [], None

        try:
            from core.mesh_texture_service import (
                build_gpu_texture_payload,
                compute_mesh_texture_report,
            )

            if parsed_mesh is None:
                from core.mesh_parser import parse_mesh
                parsed_mesh = parse_mesh(data, os.path.basename(path))
            full_mesh = parsed_mesh
            if not full_mesh.submeshes:
                return [], None

            report = compute_mesh_texture_report(
                self._active_vfs,
                self._active_vfs_path,
                full_mesh,
            )
            if not report.any_textured:
                return [], None

            # Per-face flat colours for the software viewer fallback.
            face_colors: list[tuple[int, int, int, int]] = []
            for sm, entry in zip(full_mesh.submeshes, report.submeshes):
                if entry is None:
                    face_colors.extend([(180, 180, 180, 255)] * len(sm.faces))
                else:
                    face_colors.extend(entry.face_colors)

            # GPU payload — flattened positions + UVs + texture groupings.
            payload = build_gpu_texture_payload(full_mesh, report)
            return face_colors, payload
        except Exception as exc:
            logger.debug("Preview texture colouring skipped: %s", exc)
            return [], None

    def _show_mesh_info(self, path: str) -> None:
        """Show an interactive 3D preview of the mesh.

        ── PERF (2026-05-07) ──
        The CPU portion of the mesh preview (parse + flatten + texture
        resolve) runs on a background ``FunctionWorker`` so the UI
        thread never blocks on a click. For a 33 k-vert sphere this is
        ~350 ms of pure Python work — synchronous, that's a frozen
        cursor; async, the click feels instant and the preview lands
        when the worker completes.

        Only the GL upload (``_mesh_viewer.set_mesh``) stays on the
        main thread, since the OpenGL context is bound there.

        Cancellation: when the user clicks a different file before the
        previous worker finishes, ``self._active_mesh_token`` advances
        and the in-flight worker's result is dropped on completion.
        """
        try:
            # Read upfront on the UI thread (cheap, file's typically
            # < 1 MB). The worker only does CPU work on already-loaded
            # bytes — keeps Qt's QThread story simple.
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            self._show_empty(f"Mesh read error: {e}")
            return

        # Tiny meshes prepare in <2 ms — running them on a worker just
        # adds thread-handoff overhead. Synchronous fast path skips the
        # worker entirely when the file is small enough that the worker
        # would actually slow it down.
        if len(data) < 50_000:
            self._render_prepared_mesh(self._prepare_mesh_data(data, path))
            return

        # Larger meshes go async. Bump the token first so any in-flight
        # worker's result gets dropped when it returns.
        self._active_mesh_token = getattr(self, "_active_mesh_token", 0) + 1
        token = self._active_mesh_token
        self._info_label.setText(
            f"{os.path.basename(path)}  |  Loading mesh…"
        )

        # Capture the active VFS + path so the worker doesn't read
        # them off ``self`` (which would race with the next click).
        vfs_snapshot = self._active_vfs
        vfs_path_snapshot = self._active_vfs_path

        def _worker_task(_worker, data=data, path=path,
                         vfs=vfs_snapshot, vfs_path=vfs_path_snapshot):
            # Snapshot pane state into local vars so the worker can
            # call ``_compute_preview_texture_data`` safely.
            pane_state = {
                "_active_vfs": vfs,
                "_active_vfs_path": vfs_path,
            }

            # We can't call ``self._compute_preview_texture_data``
            # directly from the worker without poking ``self`` — the
            # next click might already have flipped vfs_path. Inline
            # the resolve here using the snapshotted state.
            return _prepare_mesh_data_async(data, path, pane_state)

        from utils.thread_worker import FunctionWorker

        def _on_done(result, expected_token=token):
            if expected_token != self._active_mesh_token:
                # User clicked a newer file; drop this stale result.
                return
            self._render_prepared_mesh(result)

        worker = FunctionWorker(_worker_task)
        worker.finished_result.connect(_on_done)
        worker.error_occurred.connect(
            lambda err: self._show_empty(f"Mesh parse error: {err}"),
        )
        # Hold a reference so it isn't GC'd mid-flight.
        self._mesh_worker = worker
        worker.start()
        return

    def _prepare_mesh_data(self, data: bytes, path: str) -> dict:
        """CPU-only mesh preparation (synchronous fast path).

        Used directly for tiny meshes (where the worker overhead would
        cost more than the work itself) and as the implementation
        backend for the async worker path. Returns a dict the caller
        feeds into :meth:`_render_prepared_mesh`.
        """
        return _prepare_mesh_data_async(
            data, path,
            {"_active_vfs": self._active_vfs,
             "_active_vfs_path": self._active_vfs_path},
        )

    def _render_prepared_mesh(self, prepared: dict) -> None:
        """Apply the worker-prepared data on the UI thread.

        Only this step needs the OpenGL context and Qt main-thread
        access. Everything before it (parse, flatten, texture
        resolve, GPU buffer build) ran on the worker.
        """
        if prepared is None or "error" in prepared:
            self._show_empty(
                f"Mesh parse error: {prepared.get('error') if prepared else 'unknown'}",
            )
            return

        preview_mesh = prepared.get("preview_mesh")
        if (self._mesh_viewer is not None
                and preview_mesh is not None
                and preview_mesh.vertices and preview_mesh.faces):
            info_text = prepared["info_text"]
            face_colors = prepared["face_colors"]
            texture_payload = prepared["texture_payload"]
            if (texture_payload is not None
                    and not texture_payload.is_empty) or face_colors:
                info_text += "  |  textured"

            viewer_kwargs = {
                "info_text": info_text,
                "face_colors": face_colors,
            }
            if self._set_mesh_supports_texture_payload():
                viewer_kwargs["texture_payload"] = texture_payload

            self._mesh_viewer.set_mesh(
                preview_mesh.vertices,
                preview_mesh.faces,
                preview_mesh.normals,
                **viewer_kwargs,
            )
            # Replace any in-flight "Loading mesh…" placeholder with
            # the final status. Previously we appended, so a successful
            # render left both strings in the label (e.g. "foo.pac |
            # Loading mesh… | 7,636 verts | 9,940 faces"), which made
            # users think the mesh was still loading after it had
            # already rendered. Strict 1+1: the label always reflects
            # the current state, never history.
            file_label = os.path.basename(prepared.get("path") or "")
            if file_label:
                self._info_label.setText(f"{file_label}  |  {info_text}")
            else:
                self._info_label.setText(info_text)
            self._stack.setCurrentIndex(IDX_MESH)
            return

        # Static-image fallback (no GL viewer or empty mesh).
        from core.mesh_parser import parse_mesh
        mesh = prepared.get("full_mesh")
        if mesh is None:
            try:
                mesh = parse_mesh(
                    prepared["raw_data"],
                    os.path.basename(prepared["path"]),
                )
            except Exception as e:
                self._show_empty(f"Mesh parse error: {e}")
                return
        if not mesh.submeshes:
            self._show_empty("No geometry found in this mesh file")
            return
        pixmap = self._render_mesh_image(mesh)
        if pixmap and not pixmap.isNull():
            self._image_label.setPixmap(pixmap)
            self._info_label.setText(
                self._info_label.text() +
                f"  |  {mesh.total_vertices:,} verts  |  "
                f"{mesh.total_faces:,} faces  |  "
                f"{len(mesh.submeshes)} submesh(es)",
            )
            self._stack.setCurrentIndex(IDX_IMAGE)
        else:
            self._show_empty("Could not render mesh preview")

    def _set_mesh_supports_texture_payload(self) -> bool:
        """Cache the inspect.signature lookup. Was running every click."""
        cached = getattr(self, "_set_mesh_takes_payload", None)
        if cached is None:
            import inspect
            try:
                sig = inspect.signature(self._mesh_viewer.set_mesh)
                cached = "texture_payload" in sig.parameters
            except Exception:
                cached = False
            self._set_mesh_takes_payload = cached
        return cached

    def _render_mesh_image(self, mesh) -> "QPixmap":
        """Render mesh to a static QPixmap with shaded faces."""
        import math
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPolygonF

        w, h = 512, 512
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(24, 24, 37))

        # Merge all submesh geometry
        all_verts = []
        all_faces = []
        offset = 0
        for sm in mesh.submeshes:
            all_verts.extend(sm.vertices)
            for a, b, c in sm.faces:
                all_faces.append((a + offset, b + offset, c + offset))
            offset += len(sm.vertices)

        if not all_verts or not all_faces:
            return pixmap

        # Compute center and scale
        xs = [v[0] for v in all_verts]
        ys = [v[1] for v in all_verts]
        zs = [v[2] for v in all_verts]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        cz = (min(zs) + max(zs)) / 2
        extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 0.001)
        scale = (min(w, h) * 0.38) / extent

        # Rotation: 35° Y, -25° X for a nice 3/4 view
        ry = math.radians(35)
        rx = math.radians(-25)
        cos_y, sin_y = math.cos(ry), math.sin(ry)
        cos_x, sin_x = math.cos(rx), math.sin(rx)

        def project(vx, vy, vz):
            x = (vx - cx) * scale
            y = (vy - cy) * scale
            z = (vz - cz) * scale
            x2 = x * cos_y + z * sin_y
            z2 = -x * sin_y + z * cos_y
            y2 = y * cos_x - z2 * sin_x
            z3 = y * sin_x + z2 * cos_x
            return (w / 2 + x2, h / 2 - y2, z3)

        # Project all vertices
        projected = [project(*v) for v in all_verts]

        # Sort faces back-to-front
        light = (0.3, 0.7, 0.5)
        ln = math.sqrt(sum(l * l for l in light))
        light = tuple(l / ln for l in light)

        face_data = []
        for a, b, c in all_faces:
            if a >= len(projected) or b >= len(projected) or c >= len(projected):
                continue
            p0, p1, p2 = projected[a], projected[b], projected[c]
            avg_z = (p0[2] + p1[2] + p2[2]) / 3

            # Face normal
            v0, v1, v2 = all_verts[a], all_verts[b], all_verts[c]
            nx = (v1[1]-v0[1])*(v2[2]-v0[2]) - (v1[2]-v0[2])*(v2[1]-v0[1])
            ny = (v1[2]-v0[2])*(v2[0]-v0[0]) - (v1[0]-v0[0])*(v2[2]-v0[2])
            nz = (v1[0]-v0[0])*(v2[1]-v0[1]) - (v1[1]-v0[1])*(v2[0]-v0[0])
            nl = math.sqrt(nx*nx + ny*ny + nz*nz)
            if nl > 1e-8:
                dot = max(0.15, (nx*light[0] + ny*light[1] + nz*light[2]) / nl)
            else:
                dot = 0.4

            face_data.append((avg_z, p0, p1, p2, dot))

        face_data.sort(key=lambda f: f[0])

        # Draw
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)

        # Limit faces for performance (sample if too many)
        max_faces = 50000
        if len(face_data) > max_faces:
            step = len(face_data) // max_faces
            face_data = face_data[::step]

        for _, p0, p1, p2, dot in face_data:
            r = int(min(255, 70 + 110 * dot))
            g = int(min(255, 110 + 90 * dot))
            b_col = int(min(255, 170 + 70 * dot))

            p.setBrush(QBrush(QColor(r, g, b_col, 230)))
            p.setPen(QPen(QColor(35, 38, 52), 0.3))
            poly = QPolygonF([QPointF(p0[0], p0[1]), QPointF(p1[0], p1[1]), QPointF(p2[0], p2[1])])
            p.drawPolygon(poly)

        # Overlay info
        p.setPen(QColor(166, 173, 200))
        p.drawText(10, 20, f"{mesh.total_vertices:,} verts | {mesh.total_faces:,} faces")
        p.drawText(10, 36, f"{len(mesh.submeshes)} submesh(es) | Right-click to export OBJ/FBX")
        p.end()

        return pixmap

    def _decode_dds_native(self, path: str) -> QPixmap:
        """Decode DDS using our built-in decoder (no Pillow needed)."""
        try:
            from core.dds_reader import decode_dds_to_rgba
            from PySide6.QtGui import QImage

            with open(path, "rb") as f:
                data = f.read()

            w, h, rgba = decode_dds_to_rgba(data)
            if len(rgba) < w * h * 4:
                return QPixmap()
            img = QImage(rgba, w, h, w * 4, QImage.Format_RGBA8888)
            if img.isNull():
                return QPixmap()
            return QPixmap.fromImage(img.copy())
        except Exception:
            return QPixmap()

    def _crop_transparent_preview_bounds(self, pixmap: QPixmap) -> QPixmap:
        """Tighten image previews around visible pixels when large transparent borders exist.

        Some UI portraits/quest images are stored in a larger transparent canvas,
        which makes the real image appear as a tiny strip in the generic preview.
        """
        if pixmap.isNull():
            return pixmap

        image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        if image.isNull() or not image.hasAlphaChannel():
            return pixmap

        width = image.width()
        height = image.height()
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1

        for y in range(height):
            for x in range(width):
                if image.pixelColor(x, y).alpha() > 0:
                    if x < min_x:
                        min_x = x
                    if y < min_y:
                        min_y = y
                    if x > max_x:
                        max_x = x
                    if y > max_y:
                        max_y = y

        if max_x < min_x or max_y < min_y:
            return pixmap

        crop_width = max_x - min_x + 1
        crop_height = max_y - min_y + 1
        if crop_width >= width and crop_height >= height:
            return pixmap

        crop_area = crop_width * crop_height
        full_area = width * height
        if crop_area / max(full_area, 1) > 0.9:
            return pixmap

        return pixmap.copy(min_x, min_y, crop_width, crop_height)

    def _convert_image_with_pillow(self, path: str) -> QPixmap:
        """Convert DDS/TGA/other formats to QPixmap via Pillow."""
        try:
            from PIL import Image, ImageFile

            ImageFile.LOAD_TRUNCATED_IMAGES = True
            img = Image.open(path)
            img.load()
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
            pixmap = QPixmap.fromImage(qimg.copy())
            ImageFile.LOAD_TRUNCATED_IMAGES = False
            return pixmap
        except Exception:
            return QPixmap()

    def _show_text(self, path: str) -> None:
        """Show a text-style file in the read-only preview pane.

        Handles encoding sniffing because Pearl Abyss's encrypted-XML
        sidecars (``.pac_xml``, ``.app_xml``, ``.prefabdata_xml``,
        ``.pami``, ``.spline``, ``.mi``) ship UTF-8 with a leading BOM
        (``EF BB BF``), and a plain ``encoding='utf-8'`` decoder leaves
        that BOM as a literal ``\\ufeff`` character at the start of the
        string — which QPlainTextEdit renders as an invisible / boxy
        glyph that pushes the rest of the content off-screen on some
        Qt builds and makes the file look like it isn't loading.

        Strategy: read the bytes once, then walk a short list of
        encodings starting with ``utf-8-sig`` (which silently strips
        the UTF-8 BOM) and falling back through ``utf-16`` and
        ``cp1252`` so any future game-file encoding still renders. The
        first successful decode wins.
        """
        try:
            with open(path, "rb") as f:
                raw = f.read(2 * 1024 * 1024)
        except Exception as e:
            self._show_empty(f"Cannot read file: {e}")
            return

        content = None
        for enc in ("utf-8-sig", "utf-16", "utf-8", "cp1252"):
            try:
                content = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if content is None:
            content = raw.decode("utf-8", errors="replace")

        self._text_edit.setPlainText(content)
        self._stack.setCurrentIndex(IDX_TEXT)

    def _show_pabgb_table(self, path: str) -> None:
        """Preview a .pabgb game-data table as a formatted text summary."""
        try:
            from core.pabgb_parser import parse_pabgb, format_table_preview
            with open(path, "rb") as f:
                data = f.read()
            # Try to find matching .pabgh header alongside the temp file
            header_data = None
            header_path = path[:-1] + "h"  # .pabgb → .pabgh
            if os.path.isfile(header_path):
                with open(header_path, "rb") as f:
                    header_data = f.read()
            table = parse_pabgb(data, header_data, os.path.basename(path))
            self._text_edit.setPlainText(format_table_preview(table, max_rows=200))
            self._stack.setCurrentIndex(IDX_TEXT)
        except Exception as e:
            self._show_empty(f"Cannot parse game data table: {e}")

    def _show_hex(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                data = f.read(32768)
            lines = []
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16]
                hex_part = " ".join(f"{b:02X}" for b in chunk)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{i:08X}  {hex_part:<48s}  {ascii_part}")
            self._hex_edit.setPlainText("\n".join(lines))
            self._stack.setCurrentIndex(IDX_HEX)
        except Exception as e:
            self._show_empty(f"Cannot read file: {e}")

    def _show_font(self, path: str) -> None:
        try:
            from PySide6.QtGui import QFontDatabase
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                family = families[0] if families else "Unknown"
                preview_font = QFont(family, 24)
                sample = (
                    f"Font: {family}\n\n"
                    f"ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
                    f"abcdefghijklmnopqrstuvwxyz\n"
                    f"0123456789 !@#$%^&*()\n\n"
                    f"The quick brown fox jumps over the lazy dog."
                )
                self._font_label.setFont(preview_font)
                self._font_label.setText(sample)
            else:
                self._font_label.setText(f"Font file: {os.path.basename(path)}\n(Preview not available)")
            self._stack.setCurrentIndex(IDX_FONT)
        except Exception as e:
            self._show_empty(f"Cannot preview font: {e}")

    def _show_audio(self, path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".wem", ".bnk"):
            decoded = self._decode_wwise(path)
            if decoded:
                path = decoded
            else:
                # vgmstream not installed - auto-install silently
                self._silent_install_vgmstream(path)
                return

        ext_upper = os.path.splitext(path)[1].upper().lstrip(".")
        self._audio_info.setText(f"Audio\n{ext_upper}")
        self._audio_player.load_file(path)
        self._stack.setCurrentIndex(IDX_AUDIO)

    def _decode_wwise(self, path: str) -> str:
        """Decode a Wwise .wem/.bnk file to WAV using vgmstream-cli.

        Returns path to decoded WAV, or empty string on failure.
        """
        import subprocess
        import tempfile
        from utils.vgmstream_installer import get_vgmstream_path

        vgmstream = get_vgmstream_path()
        if not vgmstream:
            return ""

        basename = os.path.splitext(os.path.basename(path))[0]
        out_dir = tempfile.gettempdir()
        wav_path = os.path.join(out_dir, f"cf_decoded_{basename}.wav")

        if os.path.isfile(wav_path) and os.path.getsize(wav_path) > 0:
            return wav_path

        try:
            result = subprocess.run(
                [vgmstream, "-o", wav_path, path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and os.path.isfile(wav_path):
                return wav_path
        except (subprocess.TimeoutExpired, OSError):
            pass
        return ""

    def _silent_install_vgmstream(self, pending_path: str) -> None:
        """Silently auto-install vgmstream, then play the file or ask to restart."""
        if self._vgmstream_installing:
            return

        self._vgmstream_installing = True
        self._show_empty("Installing audio decoder...\nPlease wait.")
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        from utils.vgmstream_installer import install_vgmstream

        def on_progress(msg):
            self._empty_label.setText(f"Installing audio decoder...\n{msg}")
            QApplication.processEvents()

        success, message = install_vgmstream(progress_callback=on_progress)
        self._vgmstream_installing = False

        if success:
            # Try to play immediately
            decoded = self._decode_wwise(pending_path)
            if decoded:
                ext_upper = os.path.splitext(pending_path)[1].upper().lstrip(".")
                self._audio_info.setText(f"Audio\n{ext_upper}")
                self._audio_player.load_file(decoded)
                self._stack.setCurrentIndex(IDX_AUDIO)
            else:
                # Installed but decode failed - ask to restart
                self._show_empty(
                    "Audio decoder installed successfully.\n\n"
                    "Please restart the app to play Wwise audio files."
                )
        else:
            # Install failed silently - just show can't play message
            self._show_empty(
                "Could not install audio decoder automatically.\n\n"
                "To play Wwise (.wem/.bnk) files, please restart the app\n"
                "or install vgmstream manually from vgmstream.org"
            )

    def _show_video(self, path: str) -> None:
        self._video_player.setSource(QUrl.fromLocalFile(path))
        self._video_controls._update_info(path)
        self._video_player.play()
        self._stack.setCurrentIndex(IDX_VIDEO)

    def _show_web(self, path: str, ext: str) -> None:
        """Show HTML/THTML/CSS as real rendered preview using QWebEngineView."""
        if self._web_view is None:
            self._show_text(path)
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(5 * 1024 * 1024)

            if ext == ".css":
                html = self._build_css_preview(content, path)
            else:
                html = content

            base_url = QUrl.fromLocalFile(os.path.dirname(path) + "/")
            self._web_view.setHtml(html, base_url)
            self._stack.setCurrentIndex(IDX_WEB)
        except Exception:
            self._show_text(path)

    def _build_css_preview(self, css_content: str, css_path: str) -> str:
        """Build an HTML page that renders actual CSS rules with real elements."""
        import re

        rules = re.findall(
            r'([^{]+)\{([^}]*)\}',
            css_content
        )

        html_parts = [
            "<!DOCTYPE html>\n<html><head>\n<meta charset='utf-8'>\n",
            f"<style>\n{css_content}\n</style>\n",
            "<style>\n",
            "body { background: #1e1e2e; margin: 0; padding: 12px; font-family: sans-serif; }\n",
            ".cf-rule { border: 1px solid #313244; border-radius: 6px; margin: 6px 0; padding: 10px; }\n",
            ".cf-sel { font-size: 11px; color: #6c7086; font-family: monospace; margin-bottom: 4px; }\n",
            "</style>\n</head><body>\n",
        ]

        for selector_raw, props in rules:
            selector = selector_raw.strip()
            if not selector or selector.startswith("@") or selector.startswith("/*"):
                continue

            parts = [s.strip() for s in selector.split(",")]
            for sel in parts:
                sel = sel.strip()
                if not sel:
                    continue

                tag = "div"
                classes = []
                sel_id = ""

                clean = re.split(r':{1,2}[\w-]+', sel)[0].strip()

                id_match = re.search(r'#([\w-]+)', clean)
                if id_match:
                    sel_id = id_match.group(1)
                    clean = clean[:id_match.start()] + clean[id_match.end():]

                class_matches = re.findall(r'\.([\w-]+)', clean)
                classes = class_matches

                tag_match = re.match(r'^([a-zA-Z][\w]*)', clean)
                if tag_match:
                    tag = tag_match.group(1)
                    if tag in ("html", "body", "head", "style", "script", "link", "meta"):
                        continue

                if tag in ("input", "textarea", "select", "button"):
                    element_tag = tag
                elif tag in ("img",):
                    element_tag = "div"
                else:
                    element_tag = tag

                cls_attr = f" class='{' '.join(classes)}'" if classes else ""
                id_attr = f" id='{sel_id}'" if sel_id else ""

                sample_text = sel
                if element_tag == "input":
                    inner = f"<input type='text' value='{sel}'{cls_attr}{id_attr} />"
                elif element_tag == "textarea":
                    inner = f"<textarea{cls_attr}{id_attr}>{sel}</textarea>"
                elif element_tag == "button":
                    inner = f"<button{cls_attr}{id_attr}>{sel}</button>"
                elif element_tag in ("br", "hr"):
                    inner = f"<{element_tag}{cls_attr}{id_attr} />"
                else:
                    inner = f"<{element_tag}{cls_attr}{id_attr}>{sample_text}</{element_tag}>"

                html_parts.append(
                    f"<div class='cf-rule'>"
                    f"<div class='cf-sel'>{_html_escape(selector)}</div>"
                    f"{inner}"
                    f"</div>\n"
                )

        html_parts.append("</body></html>")
        return "".join(html_parts)

    def clear(self) -> None:
        """Clear the preview pane and stop any media playback."""
        self._stop_media()
        self._info_label.setText("Select a file to preview")
        self._stack.setCurrentIndex(IDX_EMPTY)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
