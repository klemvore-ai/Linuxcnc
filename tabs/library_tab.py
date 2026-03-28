import json
import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                             QTableWidgetItem, QPushButton, QLineEdit, 
                             QLabel, QComboBox, QFileDialog, QMessageBox, 
                             QHeaderView, QApplication)
from PyQt6.QtCore import Qt, QEvent, QFileSystemWatcher, QTimer
from PyQt6.QtGui import QShortcut, QKeySequence

class NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def wheelEvent(self, event):
        if self.view() and self.view().isVisible():
            super().wheelEvent(event) # Scroll normally if dropdown is open
        else:
            event.ignore() # Otherwise ignore it

class LibraryTab(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.library_data = {}
        self.undo_stack = []
        self.redo_stack = []
        self._is_restoring = False
        
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(15, 15, 15, 15)

        # Apply Dark Mode "Pro" Styling
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QLabel { font-weight: bold; font-size: 13px; color: #ecf0f1; }
            QLineEdit, QComboBox { background-color: #2d2d30; color: white; border: 1px solid #555555; padding: 6px; border-radius: 4px; font-size: 13px; }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #3498db; }
            QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 22px; border-left: 1px solid #555555; }
            QComboBox::down-arrow { image: none; width: 0px; height: 0px; border-style: solid; border-width: 5px 5px 0px 5px; border-color: #ecf0f1 rgba(0,0,0,0) rgba(0,0,0,0) rgba(0,0,0,0); margin-top: 2px; margin-right: 2px; }
            QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px; border: none; font-size: 13px; }
            QPushButton:hover { background-color: #3498db; }
            QPushButton:pressed { background-color: #1f618d; }
            QPushButton:disabled { background-color: #555555; color: #888888; }
            QTableWidget { background-color: #252526; color: #ffffff; gridline-color: #3e3e42; border: 1px solid #3e3e42; font-size: 13px; alternate-background-color: #2d2d30; }
            QTableWidget::item:selected { background-color: #2980b9; color: #ffffff; }
            QHeaderView::section { background-color: #333337; color: white; padding: 6px; border: 1px solid #3e3e42; font-weight: bold; font-size: 13px; }
            QTableCornerButton::section { background-color: #333337; border: 1px solid #3e3e42; }
            QScrollBar:vertical { background: #1e1e1e; width: 14px; margin: 0px; }
            QScrollBar::handle:vertical { background: #555555; min-height: 20px; border-radius: 7px; margin: 2px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)
        
        # --- Edit Bar ---
        edit_bar = QHBoxLayout()
        self.lib_combo = NoScrollComboBox()
        self.lib_combo.setMinimumWidth(250)
        edit_bar.addWidget(QLabel("Library:"))
        edit_bar.addWidget(self.lib_combo)
        
        btn_load = QPushButton("📂 LOAD")
        btn_load.clicked.connect(self.load_for_edit)
        edit_bar.addWidget(btn_load)
        
        btn_new = QPushButton("📄 NEW")
        btn_new.clicked.connect(self.clear_for_new)
        edit_bar.addWidget(btn_new)
        
        btn_undo = QPushButton("↶ UNDO")
        btn_undo.clicked.connect(self.undo)
        edit_bar.addWidget(btn_undo)
        
        btn_redo = QPushButton("↷ REDO")
        btn_redo.clicked.connect(self.redo)
        edit_bar.addWidget(btn_redo)
        edit_bar.addStretch()
        layout.addLayout(edit_bar)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Component Name (e.g., 7i76e-stepper)")
        self.name_input.setMinimumWidth(300)
        name_layout = QHBoxLayout()
        name_layout.addWidget(self.name_input)
        name_layout.addStretch()
        layout.addLayout(name_layout)
        
        # Search Bar for Pins
        self.pin_search = QLineEdit()
        self.pin_search.setPlaceholderText("🔍 Search pins in this component...")
        self.pin_search.setMinimumWidth(300)
        self.pin_search.textChanged.connect(self.filter_pins)
        search_layout = QHBoxLayout()
        search_layout.addWidget(self.pin_search)
        search_layout.addStretch()
        layout.addLayout(search_layout)
        
        
        # --- Table Setup: 4 Columns ---
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Pin Label", "Direction", "HAL Name", "Color"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Save state when clicking into a cell to catch edits
        self.table.cellClicked.connect(lambda r, c: self.save_undo_state())
        layout.addWidget(self.table)
        
        # --- Reorder Buttons ---
        reorder_layout = QHBoxLayout()
        btn_up = QPushButton("▲ Move Up")
        btn_down = QPushButton("▼ Move Down")
        btn_up.clicked.connect(lambda: self.move_row(-1))
        btn_down.clicked.connect(lambda: self.move_row(1))
        reorder_layout.addWidget(btn_up)
        reorder_layout.addWidget(btn_down)
        reorder_layout.addStretch()
        layout.addLayout(reorder_layout)
        
        # --- Row Management ---
        btn_row_layout = QHBoxLayout()
        btn_add_row = QPushButton("➕ Add Pin")
        btn_add_row.clicked.connect(lambda: self.add_row()) 
        btn_rem_row = QPushButton("➖ Remove Selected Pin")
        btn_rem_row.clicked.connect(self.remove_current_row)
        btn_row_layout.addWidget(btn_add_row)
        btn_row_layout.addWidget(btn_rem_row)
        btn_row_layout.addStretch()
        layout.addLayout(btn_row_layout)

        share_layout = QHBoxLayout()
        btn_export_comp = QPushButton("📤 Export (.mcomp)")
        btn_import_comp = QPushButton("📥 Import (.mcomp)")
        btn_export_comp.clicked.connect(self.export_single_component)
        btn_import_comp.clicked.connect(self.import_single_component)
        share_layout.addWidget(btn_export_comp)
        share_layout.addWidget(btn_import_comp)
        share_layout.addStretch()
        layout.insertLayout(4, share_layout) # This puts it right above the Pin Table
        
        # --- Save/Delete ---
        action_layout = QHBoxLayout()
        
        btn_save = QPushButton("💾 SAVE TO LIBRARY")
        btn_save.clicked.connect(self.save_to_library)
        btn_save.setStyleSheet("""
            QPushButton { background-color: #27ae60; color: white; min-height: 30px; font-size: 14px; font-weight: bold; border-radius: 5px; }
            QPushButton:hover { background-color: #2ecc71; }
            QPushButton:pressed { background-color: #229954; }
        """)
        action_layout.addWidget(btn_save)

        btn_delete_lib = QPushButton("🗑️ DELETE COMPONENT FROM DISK")
        btn_delete_lib.clicked.connect(self.remove_from_library)
        btn_delete_lib.setStyleSheet("""
            QPushButton { background-color: #c0392b; color: white; font-weight: bold; border-radius: 5px; }
            QPushButton:hover { background-color: #e74c3c; }
            QPushButton:pressed { background-color: #a93226; }
        """)
        action_layout.addWidget(btn_delete_lib)
        action_layout.addStretch()
        layout.addLayout(action_layout)
        
        self.refresh_list()
        
        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.fileChanged.connect(self.on_file_changed)
        self._setup_watcher()

        # --- Setup Shortcuts ---
        self.shortcut_undo = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.shortcut_undo.activated.connect(self.undo)
        
        self.shortcut_redo = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.shortcut_redo.activated.connect(self.redo)
        
        self.shortcut_redo2 = QShortcut(QKeySequence("Ctrl+Shift+Z"), self)
        self.shortcut_redo2.activated.connect(self.redo)
        
    def _setup_watcher(self):
        path = os.path.abspath("component_library.json")
        if os.path.exists(path) and path not in self.file_watcher.files():
            self.file_watcher.addPath(path)
            
    def on_file_changed(self, path):
        self.refresh_list()
        # Re-attach watcher in case the file was replaced (atomic save by other editors)
        QTimer.singleShot(500, self._setup_watcher)

    def add_row(self, label="", dir_idx=0, hal="", color="Default", save_state=True):
        if save_state:
            self.save_undo_state()
            
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        # Column 0: Label
        self.table.setItem(row, 0, QTableWidgetItem(label))
        
        # Column 1: Direction
        cb = NoScrollComboBox()
        cb.addItems(["In", "Out"])
        cb.setCurrentIndex(dir_idx)
        self.table.setCellWidget(row, 1, cb)
        
        # Column 2: HAL Name
        hal_cb = NoScrollComboBox()
        hal_cb.setEditable(True)
        # Common Mesa prefixes to save typing
        hal_cb.addItems([
            "stepgen.00.position-cmd",
            "stepgen.00.velocity-cmd",
            "stepgen.00.enable",
            "encoder.00.position",
            "encoder.00.phase-A",
            "7i76.0.0.input-00",
            "7i76.0.0.output-00",
            "pwmgen.00.value"
        ])
        hal_cb.lineEdit().setText(hal)
        self.table.setCellWidget(row, 2, hal_cb)

        # 2. CREATE THE AUTO-LABEL TRIGGER
        # This connects the "text changed" signal to our logic
        hal_cb.editTextChanged.connect(lambda text, widget=hal_cb: self.auto_label_row(widget, text))
        
        # Column 3: Color Dropdown
        color_cb = NoScrollComboBox()
        color_cb.addItems(["Default", "Power (Red)", "Signal (Blue)", "Clock (Yellow)", "Ground (Black)"])
        color_cb.setCurrentText(color)
        self.table.setCellWidget(row, 3, color_cb)

    def move_row(self, direction):
        self.save_undo_state()
        
        curr = self.table.currentRow()
        dest = curr + direction
        if 0 <= dest < self.table.rowCount():
            def get_row_data(r):
                label = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
                dir_widget = self.table.cellWidget(r, 1)
                dir_idx = dir_widget.currentIndex() if isinstance(dir_widget, QComboBox) else 0
                hal_widget = self.table.cellWidget(r, 2)
                hal = hal_widget.currentText() if isinstance(hal_widget, QComboBox) else ""
                color_widget = self.table.cellWidget(r, 3)
                color_text = color_widget.currentText() if isinstance(color_widget, QComboBox) else "Default"
                return [label, dir_idx, hal, color_text]
            
            data_curr = get_row_data(curr)
            data_dest = get_row_data(dest)
            self.apply_row_data(curr, data_dest)
            self.apply_row_data(dest, data_curr)
            self.table.setCurrentCell(dest, 0)

    def apply_row_data(self, row, data):
        self.table.setItem(row, 0, QTableWidgetItem(data[0]))
        dir_cb = NoScrollComboBox(); dir_cb.addItems(["In", "Out"]); dir_cb.setCurrentIndex(data[1])
        self.table.setCellWidget(row, 1, dir_cb)
        
        hal_cb = NoScrollComboBox()
        hal_cb.setEditable(True)
        hal_cb.addItems([
            "stepgen.00.position-cmd", "stepgen.00.velocity-cmd", "stepgen.00.enable", 
            "encoder.00.position", "encoder.00.phase-A", "7i76.0.0.input-00", 
            "7i76.0.0.output-00", "pwmgen.00.value"
        ])
        hal_cb.lineEdit().setText(data[2])
        hal_cb.editTextChanged.connect(lambda text, widget=hal_cb: self.auto_label_row(widget, text))
        self.table.setCellWidget(row, 2, hal_cb)
        
        col_cb = NoScrollComboBox(); col_cb.addItems(["Default", "Power (Red)", "Signal (Blue)", "Clock (Yellow)", "Ground (Black)"])
        col_cb.setCurrentText(data[3])
        self.table.setCellWidget(row, 3, col_cb)

    def load_for_edit(self):
        self.save_undo_state()
        
        name = self.lib_combo.currentText()
        if not name: return
        if not os.path.exists("component_library.json"): return
        with open("component_library.json", 'r') as f: data = json.load(f)
        
        pins = data.get(name, [])
        
        if len(pins) > 50:
            self.name_input.setText(f"Working... Loading {len(pins)} pins for {name}!")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            
        self.table.setRowCount(0)
        
        self.table.setUpdatesEnabled(False)
        for i, p in enumerate(pins):
            color = p[3] if len(p) > 3 else "Default"
            self.add_row(p[0], 1 if p[1] else 0, p[2], color, save_state=False)
            if i % 25 == 0:
                if len(pins) > 50:
                    self.name_input.setText(f"Loading {name}... ({i}/{len(pins)} pins)")
                QApplication.processEvents()
        self.table.setUpdatesEnabled(True)
        self.name_input.setText(name)
        if len(pins) > 50:
            QApplication.restoreOverrideCursor()

    def save_to_library(self):
        name = self.name_input.text()
        if not name: return
        pins = []
        for r in range(self.table.rowCount()):
            l = self.table.item(r, 0).text() if self.table.item(r, 0) else "Pin"
            o = self.table.cellWidget(r, 1).currentIndex() == 1
            
            hal_widget = self.table.cellWidget(r, 2)
            h = hal_widget.currentText() if isinstance(hal_widget, QComboBox) else ""
            
            c_widget = self.table.cellWidget(r, 3)
            c = c_widget.currentText() if isinstance(c_widget, QComboBox) else "Default"
            
            pins.append([l, o, h, c])
        
        lib = {}
        if os.path.exists("component_library.json"):
            with open("component_library.json", 'r') as f: lib = json.load(f)
        
        lib[name] = pins
        with open("component_library.json", 'w') as f: json.dump(lib, f, indent=4)
        
        # FIX 3: Update the Wiring Tab with the new list
        self.refresh_list() # Updates our local memory variable
        
        try:
            # Tell the Wiring tab to refresh its dropdown list immediately
            self.parent_app.wiring_tab.refresh_component_list()
        except AttributeError:
            # This just prevents a crash if WiringTab isn't ready yet
            pass

        QMessageBox.information(self, "Saved", f"'{name}' saved to library.")

    def refresh_list(self):
        current_selection = self.lib_combo.currentText()
        if os.path.exists("component_library.json"):
            try:
                with open("component_library.json", 'r') as f:
                    content = f.read()
                    if not content.strip(): return # Ignore empty reads during file locks
                    data = json.loads(content)
                    
                self.lib_combo.clear()
                self.library_data = data
                self.lib_combo.addItems(sorted(data.keys()))
                if current_selection in data:
                    self.lib_combo.setCurrentText(current_selection)

            except Exception as e:
                print(f"Error loading library: {e}")        
        else:
            self.lib_combo.clear()
            self.library_data = {}
            
        try:
            if hasattr(self, 'parent_app') and hasattr(self.parent_app, 'wiring_tab'):
                self.parent_app.wiring_tab.refresh_component_list()
        except AttributeError:
            pass

    def clear_for_new(self):
        self.save_undo_state()
        self.name_input.clear()
        self.table.setRowCount(0)

    def remove_current_row(self):
        self.save_undo_state()
        curr = self.table.currentRow()
        if curr >= 0: self.table.removeRow(curr)

    def remove_from_library(self):
        name = self.lib_combo.currentText()
        if not name: return
        confirm = QMessageBox.question(self, "Confirm Delete", f"Delete '{name}'?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            with open("component_library.json", 'r') as f: lib = json.load(f)
            if name in lib:
                del lib[name]
                with open("component_library.json", 'w') as f: json.dump(lib, f, indent=4)
            self.clear_for_new()
            self.refresh_list()
            try:
                self.parent_app.wiring_tab.refresh_component_list()
            except AttributeError:
                pass

    def export_single_component(self):
            name = self.name_input.text()
            if not name: return

            # Create folder if missing
            comp_dir = os.path.join(os.getcwd(), "exported_components")
            if not os.path.exists(comp_dir): os.makedirs(comp_dir)
            
            path, _ = QFileDialog.getSaveFileName(self, "Export Component", 
                                              os.path.join(comp_dir, f"{name}.mcomp"), 
                                              "Mesa Component (*.mcomp)")
            if path:
                pins = []
                for r in range(self.table.rowCount()):
                    l = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
                    o = self.table.cellWidget(r, 1).currentIndex() == 1
                    hal_widget = self.table.cellWidget(r, 2)
                    h = hal_widget.currentText() if isinstance(hal_widget, QComboBox) else ""
                    c = self.table.cellWidget(r, 3).currentText()
                    pins.append([l, o, h, c])
                
                with open(path, 'w') as f:
                    json.dump({name: pins}, f, indent=4)
                QMessageBox.information(self, "Success", f"Exported {name}")

    def import_single_component(self):
        comp_dir = os.path.join(os.getcwd(), "exported_components")
        if not os.path.exists(comp_dir): os.makedirs(comp_dir)

        path, _ = QFileDialog.getOpenFileName(self, "Import Component", comp_dir, "Mesa Component (*.mcomp)")
        if path:
            with open(path, 'r') as f:
                new_data = json.load(f)
            
            # Merge into main library
            lib = {}
            if os.path.exists("component_library.json"):
                with open("component_library.json", 'r') as f: lib = json.load(f)
            lib.update(new_data)
            with open("component_library.json", 'w') as f: json.dump(lib, f, indent=4)
            self.refresh_list()
            QMessageBox.information(self, "Success", "Component imported successfully!")

    def filter_pins(self, text):
        search_text = text.lower()
        for r in range(self.table.rowCount()):
            # Check Label (Col 0) and HAL Name (Col 2)
            label = self.table.item(r, 0).text().lower() if self.table.item(r, 0) else ""
            
            # Since Col 2 is a ComboBox, get its current text
            hal_widget = self.table.cellWidget(r, 2)
            hal = hal_widget.currentText().lower() if isinstance(hal_widget, QComboBox) else ""
            
            # Show row if search text is found in either column
            should_show = search_text in label or search_text in hal
            self.table.setRowHidden(r, not should_show) 

    def auto_label_row(self, hal_widget, text):
        """Automatically suggests a Pin Label based on the HAL name."""
        row = -1
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r, 2) == hal_widget:
                row = r
                break
        if row == -1: return
        
        # Only auto-label if the current Label is empty or just says "Pin"
        current_item = self.table.item(row, 0)
        current_label = current_item.text() if current_item else ""
        
        if current_label in ["", "Pin", "New Pin"]:
            # Logic: Take everything after the last dot
            # e.g., "hm2_7i76e.0.7i76.0.0.input-05" -> "input-05"
            parts = text.split('.')
            if parts:
                suggestion = parts[-1].upper() # Capitalize for that 'Pro' look
                self.table.setItem(row, 0, QTableWidgetItem(suggestion))    

    def get_state(self):
        pins = []
        for r in range(self.table.rowCount()):
            l = self.table.item(r, 0).text() if self.table.item(r, 0) else ""
            dir_widget = self.table.cellWidget(r, 1)
            o = dir_widget.currentIndex() if isinstance(dir_widget, QComboBox) else 0
            hal_widget = self.table.cellWidget(r, 2)
            h = hal_widget.currentText() if isinstance(hal_widget, QComboBox) else ""
            c_widget = self.table.cellWidget(r, 3)
            c = c_widget.currentText() if isinstance(c_widget, QComboBox) else "Default"
            pins.append([l, o, h, c])
        return {"name": self.name_input.text(), "pins": pins}

    def set_state(self, state):
        self._is_restoring = True
        self.name_input.setText(state.get("name", ""))
        self.table.setRowCount(0)
        
        self.table.setUpdatesEnabled(False)
        for i, p in enumerate(state.get("pins", [])):
            self.add_row(p[0], p[1], p[2], p[3], save_state=False)
            if i % 25 == 0:
                QApplication.processEvents()
        self.table.setUpdatesEnabled(True)
        self._is_restoring = False

    def save_undo_state(self):
        if self._is_restoring: return
        curr = self.get_state()
        if self.undo_stack and self.undo_stack[-1] == curr: return
        self.undo_stack.append(curr)
        if len(self.undo_stack) > 50: self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack: return
        self.redo_stack.append(self.get_state())
        self.set_state(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack: return
        self.undo_stack.append(self.get_state())
        self.set_state(self.redo_stack.pop())