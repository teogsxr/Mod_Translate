"""Explorer mesh Ship-to-App dialog.

Lets users package edited OBJ files as a standalone ZIP installer that
patches the correct PAZ/PAMT/PAPGT files for end users.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.mesh_ship_builder import (
    MeshShipRequest,
    build_mesh_manager_package,
    build_mesh_ship_package,
    default_mesh_ship_mod_name,
    write_mesh_manager_zip,
    write_mesh_ship_zip,
)
from core.pamt_parser import PamtFileEntry
from utils.thread_worker import FunctionWorker


class ShipMeshDialog(QDialog):
    """Generate a distributable mesh-mod ZIP from edited OBJ files."""

    def __init__(
        self,
        vfs,
        config,
        entries: list[PamtFileEntry],
        prefilled_obj_paths: dict[str, str] | None = None,
        item_index=None,
        parent=None,
    ):
        super().__init__(parent)
        self._vfs = vfs
        self._config = config
        self._entries = self._unique_entries(entries)
        self._prefilled_obj_paths = {
            key.lower(): value for key, value in (prefilled_obj_paths or {}).items()
        }
        self._item_index = item_index
        # Background worker for ZIP generation. Held on ``self`` so it
        # survives past the click handler that starts it; None when no
        # build is in flight.
        self._build_worker: FunctionWorker | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Ship Mesh Mod - Generate Package")
        self.setMinimumWidth(980)
        self.setMinimumHeight(560)

        layout = QVBoxLayout(self)

        info_group = QGroupBox("Mod Information")
        info_form = QFormLayout(info_group)

        self._mod_name = QLineEdit(self._default_mod_name())
        self._mod_name.setToolTip("Name shown in the installer window and README.")
        self._mod_name.setReadOnly(False)
        self._mod_name.setEnabled(True)
        self._mod_name.setClearButtonEnabled(True)
        self._mod_name.setFocusPolicy(Qt.StrongFocus)
        info_form.addRow("Mod Name:", self._mod_name)

        self._author = QLineEdit(self._config.get("explorer.mesh_ship.author", ""))
        self._author.setPlaceholderText("Your name, studio, or team")
        self._author.setReadOnly(False)
        self._author.setEnabled(True)
        self._author.setClearButtonEnabled(True)
        self._author.setFocusPolicy(Qt.StrongFocus)
        info_form.addRow("Author:", self._author)

        self._version = QLineEdit(self._config.get("explorer.mesh_ship.version", "1.0.0"))
        self._version.setReadOnly(False)
        self._version.setEnabled(True)
        self._version.setClearButtonEnabled(True)
        self._version.setFocusPolicy(Qt.StrongFocus)
        info_form.addRow("Version:", self._version)

        layout.addWidget(info_group)

        options_group = QGroupBox("Packaging Options")
        options_layout = QVBoxLayout(options_group)

        self._include_paired_lod = QCheckBox("Auto-include paired .pamlod when shipping a .pam mesh")
        self._include_paired_lod.setChecked(
            bool(self._config.get("explorer.mesh_ship.include_paired_lod", True))
        )
        self._include_paired_lod.setToolTip(
            "When enabled, a selected .pam will also generate and ship its matching "
            ".pamlod if one exists and was not explicitly selected."
        )
        options_layout.addWidget(self._include_paired_lod)

        self._package_mode = QComboBox()
        self._package_mode.addItem("Mod Manager ZIP (small)", "manager")
        self._package_mode.addItem("Standalone ZIP (full patched archives)", "standalone")
        saved_mode = str(self._config.get("explorer.mesh_ship.package_mode", "manager")).strip().lower()
        saved_index = 0 if saved_mode == "manager" else 1
        self._package_mode.setCurrentIndex(saved_index)
        self._package_mode.currentIndexChanged.connect(self._refresh_package_mode_ui)
        options_layout.addWidget(QLabel("Package Mode:"))
        options_layout.addWidget(self._package_mode)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #89b4fa;")
        options_layout.addWidget(self._note)

        layout.addWidget(options_group)

        assets_group = QGroupBox("Mesh Assets")
        assets_layout = QVBoxLayout(assets_group)

        self._summary = QLabel("")
        self._summary.setStyleSheet("color: #a6e3a1; font-weight: 600;")
        assets_layout.addWidget(self._summary)

        self._table = QTableWidget(len(self._entries), 6)
        self._table.setHorizontalHeaderLabels(
            ["Asset", "Group", "Format", "Edited OBJ", "Browse", "Status"]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        for row, entry in enumerate(self._entries):
            group_key = os.path.basename(os.path.dirname(entry.paz_file))
            fmt = os.path.splitext(entry.path)[1].lower().lstrip(".")
            prefilled = self._prefilled_obj_paths.get(entry.path.lower(), "")

            self._table.setItem(row, 0, self._readonly_item(entry.path))
            self._table.setItem(row, 1, self._readonly_item(group_key))
            self._table.setItem(row, 2, self._readonly_item(fmt))
            self._table.setItem(row, 3, QTableWidgetItem(prefilled))

            browse_btn = QPushButton("Browse...")
            browse_btn.clicked.connect(lambda _=False, r=row: self._browse_obj_for_row(r))
            self._table.setCellWidget(row, 4, browse_btn)
            self._table.setItem(row, 5, self._readonly_item(""))
            self._update_row_status(row)

        self._table.itemChanged.connect(self._on_table_item_changed)

        assets_layout.addWidget(self._table, 1)
        layout.addWidget(assets_group, 1)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._generate_btn = QPushButton("Generate ZIP")
        self._generate_btn.setObjectName("primary")
        self._generate_btn.clicked.connect(self._do_generate)
        btn_row.addWidget(self._generate_btn)

        layout.addLayout(btn_row)
        self._refresh_package_mode_ui()
        self._refresh_summary()

    def _readonly_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _default_mod_name(self) -> str:
        display_names = []
        seen = set()
        for entry in self._entries:
            for name in self._display_names_for_entry(entry):
                key = name.casefold()
                if key in seen:
                    continue
                seen.add(key)
                display_names.append(name)

        if len(display_names) == 1:
            return f"Crimson Desert - {display_names[0]} Mesh Mod"
        return default_mesh_ship_mod_name(self._entries)

    def _display_names_for_entry(self, entry: PamtFileEntry) -> list[str]:
        if not self._item_index or not getattr(self._item_index, "pac_to_items", None):
            return []

        base = os.path.splitext(os.path.basename(entry.path.lower()))[0]
        pac_name = base + ".pac"
        items = self._item_index.pac_to_items.get(pac_name, [])
        names = []
        seen = set()
        for item in items:
            name = (item.display_name or item.internal_name or "").strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _unique_entries(self, entries: list[PamtFileEntry]) -> list[PamtFileEntry]:
        result: list[PamtFileEntry] = []
        seen: set[str] = set()
        for entry in entries:
            key = entry.path.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(entry)
        result.sort(key=lambda item: item.path.lower())
        return result

    def _browse_obj_for_row(self, row: int) -> None:
        current = self._table.item(row, 3).text().strip()
        start_dir = os.path.dirname(current) if current else self._config.get(
            "explorer.mesh_ship.last_obj_dir",
            os.path.expanduser("~/Desktop"),
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Edited OBJ",
            start_dir,
            "OBJ Files (*.obj);;All Files (*.*)",
        )
        if not path:
            return
        self._table.item(row, 3).setText(path)
        self._config.set("explorer.mesh_ship.last_obj_dir", os.path.dirname(path))
        self._update_row_status(row)
        self._refresh_summary()

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 3:
            return
        self._update_row_status(item.row())
        self._refresh_summary()

    def _update_row_status(self, row: int) -> None:
        obj_item = self._table.item(row, 3)
        status_item = self._table.item(row, 5)
        if obj_item is None or status_item is None:
            return
        path = obj_item.text().strip()
        if not path:
            status_item.setText("Select OBJ")
            status_item.setForeground(Qt.gray)
            return
        if os.path.isfile(path):
            status_item.setText("Ready")
            status_item.setForeground(Qt.darkGreen)
            return
        status_item.setText("Missing file")
        status_item.setForeground(Qt.red)

    def _refresh_summary(self) -> None:
        ready = 0
        for row in range(self._table.rowCount()):
            status = self._table.item(row, 5).text()
            if status == "Ready":
                ready += 1
        total = self._table.rowCount()
        self._summary.setText(f"Ready assets: {ready} / {total}")

    def _package_mode_key(self) -> str:
        key = self._package_mode.currentData()
        return str(key or "manager")

    def _refresh_package_mode_ui(self) -> None:
        if self._package_mode_key() == "manager":
            self._note.setText(
                "Generates a much smaller ZIP with loose rebuilt mesh files under files/, "
                "plus manifest.json and modinfo.json for CDUMM, Crimson Browser, and "
                "other loose-file aware Crimson Desert mod managers."
            )
            self._generate_btn.setText("Generate Manager ZIP")
            return
        self._note.setText(
            "Generates patched PAZ/PAMT/PAPGT files plus install.bat, uninstall.bat, "
            "README.txt, and manifest.json for direct end-user installation."
        )
        self._generate_btn.setText("Generate Standalone ZIP")

    def _collect_requests(self) -> list[MeshShipRequest]:
        requests: list[MeshShipRequest] = []
        missing: list[str] = []
        for row, entry in enumerate(self._entries):
            obj_path = self._table.item(row, 3).text().strip()
            if not obj_path or not os.path.isfile(obj_path):
                missing.append(entry.path)
                continue
            package_group = os.path.basename(os.path.dirname(entry.paz_file))
            requests.append(
                MeshShipRequest(
                    entry=entry,
                    package_group=package_group,
                    obj_path=obj_path,
                )
            )
        if missing:
            missing_text = "\n".join(missing[:8])
            if len(missing) > 8:
                missing_text += f"\n... and {len(missing) - 8} more"
            raise ValueError(
                "Select a valid edited OBJ for every listed asset before generating.\n\n"
                f"Missing:\n{missing_text}"
            )
        return requests

    def _do_generate(self) -> None:
        """Kick off the ZIP build on a background worker.

        The build is disk-heavy: ``build_mesh_manager_package`` /
        ``build_mesh_ship_package`` re-pack PAZ/PAMT/PAPGT archives
        which can take 10-60 seconds on a full character mesh. Running
        that on the UI thread is what froze the window ("Ship to app
        seems to do nothing, bugs out the whole interface"). We now
        off-load to :class:`FunctionWorker`; the Generate button stays
        disabled and the progress bar updates live via the worker's
        ``progress`` signal.
        """
        if self._build_worker is not None and self._build_worker.isRunning():
            return   # ignore double-clicks; build already in flight

        mod_name = self._mod_name.text().strip()
        author = self._author.text().strip()
        version = self._version.text().strip()
        package_mode = self._package_mode_key()

        if not mod_name:
            QMessageBox.warning(self, "Missing", "Enter a mod name.")
            return
        if not author:
            QMessageBox.warning(self, "Missing", "Enter an author, studio, or team name.")
            return
        if not version:
            QMessageBox.warning(self, "Missing", "Enter a version.")
            return

        try:
            requests = self._collect_requests()
        except Exception as exc:
            QMessageBox.warning(self, "Missing OBJ", str(exc))
            return

        # Pre-flight memory check. We estimate peak RAM based on the
        # combined size of the OBJ(s) the user picked; the actual
        # repack holds the original PACs, the parsed meshes, and the
        # rebuilt PAZ archives in memory simultaneously. On machines
        # that are already saturated (Blender + JMM open at the same
        # time), a single extra gigabyte can push Windows into page-
        # file thrashing — the 1-FPS freeze one reporter saw.
        total_obj_size = 0
        for req in requests:
            try:
                total_obj_size += os.path.getsize(req.obj_path)
            except OSError:
                pass
        if total_obj_size > 0:
            from core.mesh_preflight import (
                MemoryStatus,
                check_memory_for_repack,
            )
            mem = check_memory_for_repack(total_obj_size)
            if mem.status == MemoryStatus.INSUFFICIENT:
                reply = QMessageBox.warning(
                    self,
                    "Low memory warning",
                    mem.recommendation + "\n\nProceed anyway?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            elif mem.status == MemoryStatus.TIGHT:
                reply = QMessageBox.warning(
                    self,
                    "Tight memory",
                    mem.recommendation + "\n\nProceed?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    return

        default_name = mod_name.replace(" ", "_").replace("-", "_")
        if package_mode == "manager":
            default_name += "_manager"
        start_dir = self._config.get(
            "explorer.mesh_ship.last_output_dir",
            os.path.expanduser("~/Desktop"),
        )
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Mesh Mod ZIP",
            os.path.join(start_dir, f"{default_name}.zip"),
            "ZIP Files (*.zip)",
        )
        if not save_path:
            return
        if not save_path.lower().endswith(".zip"):
            save_path += ".zip"

        self._config.set("explorer.mesh_ship.author", author)
        self._config.set("explorer.mesh_ship.version", version)
        self._config.set(
            "explorer.mesh_ship.include_paired_lod",
            self._include_paired_lod.isChecked(),
        )
        self._config.set("explorer.mesh_ship.package_mode", package_mode)
        self._config.set("explorer.mesh_ship.last_output_dir", os.path.dirname(save_path))
        self._config.save()

        self._generate_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status.setText("Building mesh package…")

        include_paired_lod = self._include_paired_lod.isChecked()

        # Build task runs on the worker thread. No Qt widgets are
        # touched from here — all UI updates go through the worker's
        # ``progress`` signal (marshalled to the main thread by Qt) and
        # the finished_result / error_occurred slots below.
        def task(worker: FunctionWorker):
            def progress_cb(pct: int, message: str) -> None:
                if worker.is_cancelled():
                    return
                # Cap at 90 so we have room for the final ZIP write.
                worker.report_progress(max(0, min(90, pct)), message)

            if package_mode == "manager":
                package = build_mesh_manager_package(
                    self._vfs,
                    requests,
                    mod_name=mod_name,
                    author=author,
                    version=version,
                    include_paired_lod=include_paired_lod,
                    progress_callback=progress_cb,
                )
                worker.report_progress(92, "Writing manager ZIP…")
                write_mesh_manager_zip(save_path, package, mod_name, author, version)
            else:
                package = build_mesh_ship_package(
                    self._vfs,
                    requests,
                    mod_name=mod_name,
                    author=author,
                    version=version,
                    include_paired_lod=include_paired_lod,
                    progress_callback=progress_cb,
                )
                worker.report_progress(92, "Writing standalone ZIP…")
                write_mesh_ship_zip(save_path, package, mod_name, author, version)
            worker.report_progress(100, "Mesh package ready.")
            return {
                "save_path": save_path,
                "package": package,
                "package_mode": package_mode,
            }

        worker = FunctionWorker(task)
        worker.progress.connect(self._on_progress)
        worker.finished_result.connect(self._on_build_finished)
        worker.error_occurred.connect(self._on_build_error)
        self._build_worker = worker
        worker.start()

    def _on_build_finished(self, result: dict) -> None:
        """Slot — ZIP build completed successfully."""
        self._build_worker = None
        self._generate_btn.setEnabled(True)
        self._progress.setValue(100)
        self._status.setText("Mesh package ready.")

        save_path = result["save_path"]
        package = result["package"]
        package_mode = result["package_mode"]

        if package_mode == "manager":
            QMessageBox.information(
                self,
                "Done",
                f"ZIP saved to:\n{save_path}\n\n"
                f"Assets: {package.manifest['asset_count']}\n"
                f"Loose files: {package.manifest['file_count']}\n\n"
                "Import this ZIP into CDUMM, Crimson Browser, or another loose-file aware mod manager.",
            )
        else:
            QMessageBox.information(
                self,
                "Done",
                f"ZIP saved to:\n{save_path}\n\n"
                f"Assets: {package.manifest['asset_count']}\n"
                f"Patched archive files: {package.manifest['archive_file_count']}\n\n"
                "End users can extract the ZIP and run install.bat.",
            )
        self._progress.setVisible(False)
        self.accept()

    def _on_build_error(self, error_msg: str) -> None:
        """Slot — worker raised. Surface the error + re-enable the button."""
        self._build_worker = None
        self._generate_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText("Mesh package failed.")
        QMessageBox.critical(self, "Mesh Ship Error", error_msg)

    def _on_progress(self, pct: int, message: str) -> None:
        self._progress.setValue(max(0, min(100, pct)))
        self._status.setText(message)

    def reject(self) -> None:
        """Guard against closing while a worker is still running."""
        if self._build_worker is not None and self._build_worker.isRunning():
            self._build_worker.request_cancel()
            self._build_worker.wait(3000)
        super().reject()
