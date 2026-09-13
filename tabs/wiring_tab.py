from PyQt6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, 
                             QComboBox, QTextEdit, QMessageBox, QFileDialog, QApplication, QInputDialog, QColorDialog, QDialog)
from PyQt6.QtCore import Qt, QRectF, QTimer
from PyQt6.QtGui import QColor
from core.canvas import MesaCanvas
from core.components import MesaComponent, Pin, Wire, ComponentZone
from core.hal_documents import HALDocumentComposer, HALDocumentError
from core.machine_agent import client_from_settings
import time
import json
import os

class WiringTab(QWidget):
    def __init__(self, library_tab):
        super().__init__()
        self.library_tab = library_tab
        self.first_pin = None
        self.undo_stack = []
        self.redo_stack = []
        self.trash = [] # Hidden bin to prevent Garbage Collection crashes
        self.init_ui()
        self.refresh_component_list()

    def init_ui(self):
        layout = QHBoxLayout(self)
        
        # Sidebar
        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("<b>Logic Designer</b>"))
        
        self.comp_selector = QComboBox()
        sidebar.addWidget(self.comp_selector)
        
        btn_add = QPushButton("➕ Add Component")
        btn_add.clicked.connect(self.add_comp)
        sidebar.addWidget(btn_add)
        
        btn_rem = QPushButton("➖ Remove Selected")
        btn_rem.clicked.connect(self.delete_selected_components)
        sidebar.addWidget(btn_rem)
        
        btn_group = QPushButton("🗂️ Auto-Group Zones")
        btn_group.setStyleSheet("background: #d35400; color: white; padding: 8px; font-weight: bold;")
        btn_group.clicked.connect(self.auto_group_zones)
        sidebar.addWidget(btn_group)
        
        btn_clear = QPushButton("🗑️ Clear Canvas")
        btn_clear.clicked.connect(self.clear_canvas)
        sidebar.addWidget(btn_clear)
        
        sidebar.addSpacing(20)
        
        btn_save = QPushButton("💾 Save Project")
        btn_save.clicked.connect(self.save_proj)
        sidebar.addWidget(btn_save)
        
        btn_load = QPushButton("📂 Load Project")
        btn_load.clicked.connect(self.load_proj)
        sidebar.addWidget(btn_load)
        
        sidebar.addSpacing(20)
        
        btn_export = QPushButton("📤 Export to .HAL")
        btn_export.setStyleSheet("background: #2980b9; color: white; padding: 8px; font-weight: bold;")
        btn_export.clicked.connect(self.export_hal)
        sidebar.addWidget(btn_export)
        
        btn_import_hal = QPushButton("📥 Import .HAL")
        btn_import_hal.setStyleSheet("background: #16a085; color: white; padding: 8px; font-weight: bold;")
        btn_import_hal.clicked.connect(self.import_hal)
        sidebar.addWidget(btn_import_hal)

        btn_preview = QPushButton("👁️ Preview HAL")
        btn_preview.setStyleSheet("background: #27ae60; color: white; padding: 8px; font-weight: bold;")
        btn_preview.clicked.connect(self.preview_hal)
        sidebar.addWidget(btn_preview)
        
        btn_deploy = QPushButton("🚀 Deploy to LinuxCNC")
        btn_deploy.setStyleSheet("background: #8e44ad; color: white; padding: 8px; font-weight: bold;")
        btn_deploy.clicked.connect(self.deploy_hal)
        sidebar.addWidget(btn_deploy)
        
        sidebar.addStretch()
        layout.addLayout(sidebar, 1)

        # Canvas & Console View
        canvas_layout = QVBoxLayout()
        self.canvas = MesaCanvas()
        self.canvas.mousePressEvent = self.canvas_mouse_press
        self.canvas.mouseDoubleClickEvent = self.canvas_mouse_double_click
        self.canvas.keyPressEvent = self.canvas_key_press
        canvas_layout.addWidget(self.canvas, 4)
        
        self.console = QTextEdit()
        self.console.setFixedHeight(120)
        self.console.setReadOnly(True)
        self.console.setStyleSheet("background: #1e1e1e; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace;")
        canvas_layout.addWidget(self.console, 1)
        
        layout.addLayout(canvas_layout, 5)

    def refresh_component_list(self):
        self.comp_selector.clear()
        self.comp_selector.addItems(self.library_tab.library_data.keys())

    def add_comp(self):
        name = self.comp_selector.currentText()
        if name in self.library_tab.library_data:
            pins = self.library_tab.library_data[name]
            if len(pins) > 50:
                self.console.append(f"⏳ Generating {name} with {len(pins)} pins... This is a huge component, please wait!")
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                QApplication.processEvents()
                
            self.save_undo_state()
            comp_id = f"{name}_{int(time.time())}"
            comp = MesaComponent(name, 100, 100, pins, comp_id)
            self.canvas.scene.addItem(comp)
            self.console.append(f"✅ Added: {name}")
            if len(pins) > 50:
                QApplication.restoreOverrideCursor()

    def clear_canvas(self):
        self.save_undo_state()
        grabber = self.canvas.scene.mouseGrabberItem()
        if grabber: grabber.ungrabMouse()
            
        self.trash.extend(self.canvas.scene.items())
        self.canvas.scene.clear()
        if len(self.trash) > 100: self.trash = self.trash[-50:]
        self.console.append("Canvas cleared.")

    def get_canvas_state(self):
        """Serializes the current canvas to a dictionary for Save and Undo functions."""
        project_data = {"components": [], "wires": [], "zones": []}
        for item in self.canvas.scene.items():
            if isinstance(item, MesaComponent):
                project_data["components"].append({
                    "name": item.name,
                    "comp_id": item.comp_id,
                    "x": item.pos().x(),
                    "y": item.pos().y()
                })
            elif isinstance(item, Wire):
                start_comp = item.start_pin.parentItem()
                end_comp = item.end_pin.parentItem()
                if not isinstance(start_comp, MesaComponent) or not isinstance(end_comp, MesaComponent):
                    continue
                project_data["wires"].append({
                    "start_comp_id": start_comp.comp_id,
                    "start_pin_idx": start_comp.pins.index(item.start_pin),
                    "end_comp_id": end_comp.comp_id,
                    "end_pin_idx": end_comp.pins.index(item.end_pin),
                    "color": item.base_color.name()
                })
            elif isinstance(item, ComponentZone):
                project_data.setdefault("zones", []).append({
                    "title": item.title.toPlainText(),
                    "x": item.rect().x(),
                    "y": item.rect().y(),
                    "w": item.rect().width(),
                    "h": item.rect().height()
                })
        
        # Sort to ensure consistent equality checks (so hovering/clicking doesn't trigger fake Undos)
        project_data["components"] = sorted(project_data["components"], key=lambda k: k["comp_id"])
        project_data["wires"] = sorted(project_data["wires"], key=lambda k: (k["start_comp_id"], k["end_comp_id"], k["start_pin_idx"], k["end_pin_idx"]))
        return project_data

    def set_canvas_state(self, project_data):
        """Restores the canvas from a serialized dictionary for Load and Undo/Redo functions."""
        grabber = self.canvas.scene.mouseGrabberItem()
        if grabber: grabber.ungrabMouse()
            
        self.canvas.scene.clear()
        self.first_pin = None
        
        for zone_data in project_data.get("zones", []):
            rect = QRectF(zone_data["x"], zone_data["y"], zone_data["w"], zone_data["h"])
            zone = ComponentZone(rect, zone_data.get("title", "Network Zone"))
            self.canvas.scene.addItem(zone)
            
        comp_map = {}
        for comp_data in project_data.get("components", []):
            name = comp_data.get("name")
            comp_id = comp_data.get("comp_id")
            x = comp_data.get("x", 0)
            y = comp_data.get("y", 0)
            
            if name in self.library_tab.library_data:
                comp = MesaComponent(name, x, y, self.library_tab.library_data[name], comp_id)
                self.canvas.scene.addItem(comp)
                comp_map[comp_id] = comp
            else:
                self.console.append(f"Warning: Component '{name}' not found in library. Skipping.")

        for wire_data in project_data.get("wires", []):
            start_id = wire_data.get("start_comp_id")
            end_id = wire_data.get("end_comp_id")
            start_idx = wire_data.get("start_pin_idx")
            end_idx = wire_data.get("end_pin_idx")
            
            if start_id in comp_map and end_id in comp_map:
                start_comp = comp_map[start_id]
                end_comp = comp_map[end_id]
                
                if start_idx < len(start_comp.pins) and end_idx < len(end_comp.pins):
                    start_pin = start_comp.pins[start_idx]
                    end_pin = end_comp.pins[end_idx]
                    color = wire_data.get("color", "#f39c12")
                    wire = Wire(start_pin, end_pin, color)
                    self.canvas.scene.addItem(wire)
                else:
                    self.console.append("Warning: Pin index out of bounds. Skipping wire.")
            else:
                self.console.append("Warning: Wire connection missing component. Skipping.")

    def save_undo_state(self):
        current_state = self.get_canvas_state()
        if self.undo_stack and self.undo_stack[-1] == current_state:
            return # Ignore if nothing actually changed
        self.undo_stack.append(current_state)
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0) # Limit history to 50 actions to save memory
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.get_canvas_state())
        self.set_canvas_state(self.undo_stack.pop())
        self.console.append("Undo Executed.")

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.get_canvas_state())
        self.set_canvas_state(self.redo_stack.pop())
        self.console.append("Redo Executed.")

    def save_proj(self):
        settings = self.library_tab.parent_app.settings
        default_dir = os.path.join(os.getcwd(), "projects")
        last_dir = settings.get_setting("PATHS", "last_project_dir", default_dir)
        if not os.path.exists(last_dir):
            last_dir = default_dir
            os.makedirs(last_dir, exist_ok=True)
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", last_dir, "LinuxCNC Project (*.linuxcnc)")
        if not path: return
        
        if not path.endswith('.linuxcnc'):
            path += '.linuxcnc'
        
        settings.save_setting("PATHS", "last_project_dir", os.path.dirname(path))
        
        try:
            with open(path, 'w') as f:
                json.dump(self.get_canvas_state(), f, indent=4)
            self.console.append(f"Project saved successfully to {path}")
            QMessageBox.information(self, "Save", f"Project layout saved to:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project:\n{e}")

    def load_proj(self):
        settings = self.library_tab.parent_app.settings
        default_dir = os.path.join(os.getcwd(), "projects")
        last_dir = settings.get_setting("PATHS", "last_project_dir", default_dir)
        if not os.path.exists(last_dir):
            last_dir = default_dir
            os.makedirs(last_dir, exist_ok=True)
            
        path, _ = QFileDialog.getOpenFileName(self, "Load Project", last_dir, "LinuxCNC Project (*.linuxcnc);;All Files (*)")
        if not path: return
        
        settings.save_setting("PATHS", "last_project_dir", os.path.dirname(path))
        
        try:
            with open(path, 'r') as f: project_data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load project:\n{e}")
            return

        self.save_undo_state()
        self.console.append(f"Loading project from {path}...")
        self.set_canvas_state(project_data)
        self.console.append("Project loaded successfully.")
        QMessageBox.information(self, "Load", "Project data restored from disk.")

    def get_ui_pins(self):
        ui_pins = {}
        for item in self.canvas.scene.items():
            if isinstance(item, MesaComponent):
                for pin in getattr(item, 'pins', []):
                    hal_name = getattr(pin, 'hal_name', '').strip()
                    if hal_name.startswith("ui."):
                        ptype = "bit" if pin.brush().color().name() in ["#3498db", "#e74c3c", "#95a5a6"] else "float"
                        ui_pins[hal_name] = (pin.is_out, ptype)
        return ui_pins

    def build_hal_header(self, ui_pins):
        if not ui_pins: return []
        lines = ["# --- Auto-Generated UI Virtual Pins ---", "loadrt linuxcnc_ui", ""]
        for hal_name in ui_pins:
            clean_name = hal_name.replace("ui.", "").replace(".", "_").replace("-", "_")
            lines.append(f"alias pin linuxcnc_ui.0.{clean_name} {hal_name}")
        lines.append("")
        return lines

    def build_comp_file(self, ui_pins):
        if not ui_pins: return ""
        lines = ['component linuxcnc_ui "LinuxCNC Virtual UI Pins";', '']
        for hal_name, (is_out, ptype) in ui_pins.items():
            clean_name = hal_name.replace("ui.", "").replace(".", "_").replace("-", "_")
            if is_out:
                lines.append(f"pin out {ptype} {clean_name};")
                lines.append(f"param rw {ptype} {clean_name}_cmd;")
            else:
                lines.append(f"pin in {ptype} {clean_name};")
            
        lines.extend(['', 'function _ nofp;', 'license "GPL";', ';;', 
                      'FUNCTION(_) {'])
                      
        for hal_name, (is_out, ptype) in ui_pins.items():
            clean_name = hal_name.replace("ui.", "").replace(".", "_").replace("-", "_")
            if is_out:
                lines.append(f"    {clean_name} = {clean_name}_cmd;")
        lines.append('}')
        return "\n".join(lines)

    def align_hal_lines(self, lines):
        max_sig, max_src = 0, 0
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("net "):
                parts = line_str.split("=>")
                left = parts[0].strip().split()
                if len(left) >= 3:
                    max_sig = max(max_sig, len(left[1]))
                    max_src = max(max_src, len(" ".join(left[2:])))
        out_lines = []
        for line in lines:
            indent = line[:len(line) - len(line.lstrip())]
            line_str = line.strip()
            if line_str.startswith("net "):
                parts = line_str.split("=>")
                left = parts[0].strip().split()
                if len(left) >= 3:
                    sig = left[1].ljust(max_sig)
                    src = " ".join(left[2:]).ljust(max_src)
                    dest = parts[1].strip() if len(parts) > 1 else ""
                    out_lines.append(f"{indent}net {sig}  {src}  =>  {dest}" if dest else f"{indent}net {sig}  {src}")
                else: out_lines.append(line)
            else: out_lines.append(line)
        return out_lines

    def export_hal(self):
        settings = self.library_tab.parent_app.settings
        default_dir = os.path.join(os.getcwd(), "exports")
        last_dir = settings.get_setting("PATHS", "last_export_dir", default_dir)
        if not os.path.exists(last_dir):
            last_dir = default_dir
            os.makedirs(last_dir, exist_ok=True)
            
        path, _ = QFileDialog.getSaveFileName(self, "Export HAL", os.path.join(last_dir, "custom_postgui.hal"), "HAL Files (*.hal)")
        if not path:
            return
        settings.save_setting("PATHS", "last_export_dir", os.path.dirname(path))

        self.console.clear()
        
        ui_pins = self.get_ui_pins()
        comp_code = self.build_comp_file(ui_pins)
        if comp_code:
            comp_path = os.path.join(os.path.dirname(path), "linuxcnc_ui.comp")
            try:
                with open(comp_path, 'w') as f: f.write(comp_code)
                self.console.append(f"Generated UI dummy component: {comp_path}")
            except Exception as e:
                self.console.append(f"Failed to write comp file: {e}")
        
        processed_wires = set()
        hal_lines = ["# --- Auto-Generated HAL Output ---"]
        hal_lines.extend(self.build_hal_header(ui_pins))
        
        for item in self.canvas.scene.items():
            if isinstance(item, Wire) and item not in processed_wires:
                processed_wires.add(item)
                
                # Determine direction for standard 'net signal out_pin => in_pin'
                out_pin = item.start_pin if item.start_pin.is_out else item.end_pin
                in_pin = item.end_pin if not item.end_pin.is_out else item.start_pin
                
                # Fetch HAL identifiers
                out_hal = getattr(out_pin, 'hal_name', '').strip()
                in_hal = getattr(in_pin, 'hal_name', '').strip()
                
                # Filter out visual-only connections where a HAL pin is missing
                if not out_hal or not in_hal:
                    continue
                
                # Generate a safe signal name based on the output component and pin label
                comp_name = out_pin.parentItem().title.toPlainText().replace(' ', '_').replace('-', '_')
                pin_lbl = getattr(out_pin, 'label', 'pin').replace(' ', '_').replace('-', '_')
                
                # Clean up characters that might be invalid in HAL signal names
                for char in ['[', ']', ',', '/', '(', ')']:
                    pin_lbl = pin_lbl.replace(char, '')
                    
                signal_name = f"sig_{comp_name}_{pin_lbl}".lower()
                
                line = f"net {signal_name} {out_hal} => {in_hal}"
                self.console.append(line)
                hal_lines.append(line)
                
        hal_lines = self.align_hal_lines(hal_lines)
                
        try:
            composer = HALDocumentComposer(os.path.dirname(path))
            filename = os.path.basename(path)
            artifacts = composer.compose("", "", "\n".join(hal_lines))
            composer.artifact_path(filename).write_text(
                artifacts["custom_postgui.hal"],
                encoding="utf-8",
                newline="\n",
            )
            self.console.append(f"Successfully exported HAL to: {path}")
            QMessageBox.information(self, "Export Successful", f"HAL file exported to:\n{path}")
        except (HALDocumentError, OSError) as e:
            QMessageBox.critical(self, "Export Error", f"Failed to save HAL file:\n{e}")

    def import_hal(self, path=None, auto_layout=False):
        settings = self.library_tab.parent_app.settings
        if not path:
            default_dir = os.path.join(os.getcwd(), "exports")
            last_dir = settings.get_setting("PATHS", "last_import_hal_dir", default_dir)
            if not os.path.exists(last_dir): last_dir = default_dir
            path, _ = QFileDialog.getOpenFileName(self, "Import HAL", last_dir, "HAL Files (*.hal);;All Files (*)")
            if not path: return
            settings.save_setting("PATHS", "last_import_hal_dir", os.path.dirname(path))
        
        try:
            with open(path, 'r') as f: lines = f.readlines()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file:\n{e}")
            return
            
        self.console.append(f"\n--- Parsing {os.path.basename(path)} ---")
        
        new_components = {}
        parsed_nets = {}
        
        for raw_line in lines:
            line = raw_line.split('#')[0].strip()
            if not line.startswith("net "): continue
                
            parts = line.split()
            if len(parts) < 3: continue
                
            signal_name = parts[1]
            current_dir_is_out = True # First pin in a net is usually the source (Out)
            
            for part in parts[2:]:
                if part in ["=>", "->"]: current_dir_is_out = False; continue
                if part in ["<=", "<-"]: current_dir_is_out = True; continue
                    
                hal_name = part
                if '.' in hal_name:
                    comp_name = hal_name.rsplit('.', 1)[0]
                    pin_label = hal_name.rsplit('.', 1)[1].upper()
                else:
                    comp_name = "Misc_Imported"
                    pin_label = hal_name.upper()
                    
                if comp_name not in new_components:
                    new_components[comp_name] = []
                
                if not any(p[2] == hal_name for p in new_components[comp_name]):
                    color = "Default"
                    if any(x in pin_label.lower() for x in ["step", "dir", "pwm"]): color = "Clock (Yellow)"
                    elif any(x in pin_label.lower() for x in ["enable", "ena", "is-on"]): color = "Signal (Blue)"
                    new_components[comp_name].append([pin_label, current_dir_is_out, hal_name, color])
                
                if signal_name not in parsed_nets: parsed_nets[signal_name] = []
                parsed_nets[signal_name].append(hal_name)
                
                current_dir_is_out = False # Subsequent pins are usually receivers (In)
                
        existing_lib = self.library_tab.library_data
        added_count = 0
        lib_to_save = dict(existing_lib)
        
        # Cross-reference with Library to find unmapped pins
        for comp, pins in new_components.items():
            missing_pins = [p for p in pins if not any(any(lp[2] == p[2] for lp in lib_pins) for lib_pins in existing_lib.values())]
                    
            if missing_pins:
                clean_comp_name = f"Auto_{comp.replace('.', '_')}"
                if clean_comp_name not in lib_to_save: lib_to_save[clean_comp_name] = []
                lib_to_save[clean_comp_name].extend(missing_pins)
                added_count += 1
                
        if added_count > 0:
            with open("component_library.json", 'w') as f: json.dump(lib_to_save, f, indent=4)
            self.library_tab.refresh_list()
            self.refresh_component_list()
            self.console.append(f"Added {added_count} new component groups to the Library based on missing HAL pins!")
            
        self.console.append(f"Successfully parsed {len(parsed_nets)} signal nets from the HAL file.")
        
        # --- Phase 2: Auto-Spawn Components ---
        components_to_spawn = set()
        for hal_names in parsed_nets.values():
            for hal in hal_names:
                for lib_name, lib_pins in lib_to_save.items():
                    if any(p[2] == hal for p in lib_pins):
                        components_to_spawn.add(lib_name)
                        break # Found the parent component, move to the next pin

        if components_to_spawn:
            if not auto_layout:
                reply = QMessageBox.question(self, "Spawn Components", 
                    f"Found {len(components_to_spawn)} components required for this configuration.\nWould you like to automatically drop them onto the canvas?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                spawn = (reply == QMessageBox.StandardButton.Yes)
            else:
                spawn = True
                
            if spawn:
                self.save_undo_state()
                
                left_x, left_y = 50, 50
                right_x, right_y = 500, 50
                
                for lib_name in components_to_spawn:
                    pins = lib_to_save[lib_name]
                    comp_id = f"{lib_name}_{time.time_ns()}"
                    
                    # Smart Auto-Layout: Mesa/Hardware on the left, Peripherals/Logic on the right
                    if "mesa" in lib_name.lower() or "auto_" in lib_name.lower():
                        x, y = left_x, left_y
                        left_y += (len(pins) * 28) + 80
                    else:
                        x, y = right_x, right_y
                        right_y += (len(pins) * 28) + 80
                        
                    comp = MesaComponent(lib_name, x, y, pins, comp_id)
                    self.canvas.scene.addItem(comp)
                    
                self.console.append(f"✅ Dropped {len(components_to_spawn)} components onto the canvas.")
                self.canvas.scene.update()

        # --- Phase 3: Auto-Wire Components ---
        hal_to_pin = {}
        for item in self.canvas.scene.items():
            if isinstance(item, MesaComponent):
                for pin in item.pins:
                    if getattr(pin, 'hal_name', '').strip():
                        hal_to_pin[pin.hal_name.strip()] = pin
                        
        wires_added = 0
        for signal_name, hal_names in parsed_nets.items():
            net_pins = [hal_to_pin[h] for h in hal_names if h in hal_to_pin]
            if len(net_pins) < 2:
                continue
                
            sources = [p for p in net_pins if p.is_out]
            dests = [p for p in net_pins if not p.is_out]
            
            # Fallback for directionless nets
            if not sources and dests: sources = [dests.pop(0)]
            elif sources and not dests: dests = sources[1:]; sources = [sources[0]]
                
            if sources and dests:
                src = sources[0]
                for dst in dests:
                    # Prevent duplicate wires
                    if not any(w.end_pin == dst or w.start_pin == dst for w in src.wires):
                        color = src.brush().color().name() # Inherit the source pin's color!
                        wire = Wire(src, dst, color)
                        self.canvas.scene.addItem(wire)
                        wires_added += 1

        if wires_added > 0:
            self.console.append(f"✅ Auto-wired {wires_added} connections.")
            self.canvas.scene.update()

        if auto_layout:
            self.auto_group_zones()
        else:
            QMessageBox.information(self, "Import Successful", f"Parsed {len(parsed_nets)} networks.\nAdded {added_count} new component groupings.\nSpawned {len(components_to_spawn)} blocks.\nAuto-wired {wires_added} connections.")

    def preview_hal(self):
        ui_pins = self.get_ui_pins()
        processed_wires = set()
        hal_lines = ["# --- Auto-Generated LinuxCNC HAL Output ---"]
        hal_lines.extend(self.build_hal_header(ui_pins))
        
        for item in self.canvas.scene.items():
            if isinstance(item, Wire) and item not in processed_wires:
                processed_wires.add(item)
                out_pin = item.start_pin if item.start_pin.is_out else item.end_pin
                in_pin = item.end_pin if not item.end_pin.is_out else item.start_pin
                
                out_hal = getattr(out_pin, 'hal_name', '').strip()
                in_hal = getattr(in_pin, 'hal_name', '').strip()
                
                if not out_hal or not in_hal:
                    continue
                
                comp_name = out_pin.parentItem().title.toPlainText().replace(' ', '_').replace('-', '_')
                pin_lbl = getattr(out_pin, 'label', 'pin').replace(' ', '_').replace('-', '_')
                
                for char in ['[', ']', ',', '/', '(', ')']:
                    pin_lbl = pin_lbl.replace(char, '')
                    
                signal_name = f"sig_{comp_name}_{pin_lbl}".lower()
                hal_lines.append(f"net {signal_name} {out_hal} => {in_hal}")

        hal_lines = self.align_hal_lines(hal_lines)

        if len(hal_lines) <= 1:
            QMessageBox.information(self, "Preview", "No connections to preview!")
            return

        preview_dlg = QDialog(self)
        preview_dlg.setWindowTitle("HAL Code Preview")
        preview_dlg.resize(700, 500)
        dlg_layout = QVBoxLayout(preview_dlg)
        
        preview_text = QTextEdit()
        preview_text.setPlainText("\n".join(hal_lines))
        preview_text.setReadOnly(True)
        preview_text.setStyleSheet("background: #1e1e1e; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px;")
        dlg_layout.addWidget(preview_text)
        
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(preview_dlg.accept)
        dlg_layout.addWidget(btn_close)
        
        preview_dlg.exec()

    def auto_group_zones(self):
        # 1. Clear out any existing zones
        for item in self.canvas.scene.items():
            if isinstance(item, ComponentZone):
                self.canvas.scene.removeItem(item)
                
        # 2. Build adjacency graph of connected components
        components = [item for item in self.canvas.scene.items() if isinstance(item, MesaComponent)]
        adj = {c: set() for c in components}
        
        for item in self.canvas.scene.items():
            if isinstance(item, Wire):
                c1, c2 = item.start_pin.parentItem(), item.end_pin.parentItem()
                if c1 in adj and c2 in adj:
                    adj[c1].add(c2)
                    adj[c2].add(c1)
                    
        # 3. Pathfinding to isolate discrete clusters
        visited = set()
        clusters = []
        for c in components:
            if c not in visited:
                cluster = []
                queue = [c]
                visited.add(c)
                while queue:
                    curr = queue.pop(0)
                    cluster.append(curr)
                    for neighbor in adj[curr]:
                        if neighbor not in visited:
                            visited.add(neighbor)
                            queue.append(neighbor)
                if len(cluster) > 1: # Only frame it if there are actually wires connecting them
                    clusters.append(cluster)
                    
        # 4. Generate Bounding Boxes
        for i, cluster in enumerate(clusters):
            min_x, min_y = min(c.sceneBoundingRect().left() for c in cluster), min(c.sceneBoundingRect().top() for c in cluster)
            max_x, max_y = max(c.sceneBoundingRect().right() for c in cluster), max(c.sceneBoundingRect().bottom() for c in cluster)
            rect = QRectF(min_x - 40, min_y - 50, (max_x - min_x) + 80, (max_y - min_y) + 75)
            self.canvas.scene.addItem(ComponentZone(rect, f"Network Zone {i+1}"))
            
        self.console.append(f"🗂️ Generated {len(clusters)} visual grouping zones.")
        self.canvas.scene.update()

    def deploy_hal(self):
        settings = self.library_tab.parent_app.settings
        if client_from_settings(settings) is not None:
            QMessageBox.information(
                self,
                "Secure Deployment Required",
                "Secure mode does not permit arbitrary HAL compilation or remote shell deployment. "
                "Deploy approved configuration artifacts through the Settings configuration workflow.",
            )
            return

        ui_pins = self.get_ui_pins()
        processed_wires = set()
        hal_lines = ["# --- Auto-Generated LinuxCNC HAL Output ---"]
        hal_lines.extend(self.build_hal_header(ui_pins))
        
        for item in self.canvas.scene.items():
            if isinstance(item, Wire) and item not in processed_wires:
                processed_wires.add(item)
                out_pin = item.start_pin if item.start_pin.is_out else item.end_pin
                in_pin = item.end_pin if not item.end_pin.is_out else item.start_pin
                
                out_hal = getattr(out_pin, 'hal_name', '').strip()
                in_hal = getattr(in_pin, 'hal_name', '').strip()
                
                if not out_hal or not in_hal:
                    continue
                
                comp_name = out_pin.parentItem().title.toPlainText().replace(' ', '_').replace('-', '_')
                pin_lbl = getattr(out_pin, 'label', 'pin').replace(' ', '_').replace('-', '_')
                
                for char in ['[', ']', ',', '/', '(', ')']:
                    pin_lbl = pin_lbl.replace(char, '')
                    
                signal_name = f"sig_{comp_name}_{pin_lbl}".lower()
                hal_lines.append(f"net {signal_name} {out_hal} => {in_hal}")

        hal_lines = self.align_hal_lines(hal_lines)

        if len(hal_lines) <= 1:
            QMessageBox.warning(self, "Empty", "No connections to deploy!")
            return

        hal_content = "\n".join(hal_lines)
        
        preview_dlg = QDialog(self)
        preview_dlg.setWindowTitle("HAL Code Preview")
        preview_dlg.resize(700, 500)
        dlg_layout = QVBoxLayout(preview_dlg)
        
        info_label = QLabel("Review your generated HAL code below. You can make quick manual tweaks before deploying.")
        info_label.setStyleSheet("color: #ecf0f1; font-style: italic;")
        dlg_layout.addWidget(info_label)
        
        preview_text = QTextEdit()
        preview_text.setPlainText(hal_content)
        preview_text.setStyleSheet("background: #1e1e1e; color: #00ff00; font-family: 'Consolas', 'Courier New', monospace; font-size: 13px;")
        dlg_layout.addWidget(preview_text)
        
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(preview_dlg.reject)
        
        btn_confirm = QPushButton("🚀 Proceed to Deploy")
        btn_confirm.setStyleSheet("background: #8e44ad; color: white; padding: 6px; font-weight: bold;")
        btn_confirm.clicked.connect(preview_dlg.accept)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_confirm)
        dlg_layout.addLayout(btn_layout)
        
        if not preview_dlg.exec():
            return
            
        # Re-capture the text in case the user made manual edits in the preview window
        hal_content = preview_text.toPlainText()
        
        mach_name = settings.get_setting('MACHINE_CONFIG', 'name', 'LinuxCNC')
        default_path = settings.get_setting("DEPLOY", "remote_path", f"~/linuxcnc/configs/{mach_name}/custom_postgui.hal")
        
        remote_path, ok = QInputDialog.getText(self, "Deploy to LinuxCNC", 
                                               "Enter remote file path:", text=default_path)
        if not ok or not remote_path: return
            
        settings.save_setting("DEPLOY", "remote_path", remote_path)
        mesa_tab = self.library_tab.parent_app.mesa_util_tab
        
        comp_code = self.build_comp_file(ui_pins)
        if comp_code:
            self.console.append("\n🚀 Compiling linuxcnc_ui dummy component on remote machine...")
            comp_cmd = f"cat << 'EOF' > /tmp/linuxcnc_ui.comp\n{comp_code}\nEOF\nsudo halcompile --install /tmp/linuxcnc_ui.comp"
            out, err = mesa_tab.execute_remote_command(comp_cmd, use_sudo=True)
            if err and "error" in err.lower():
                QMessageBox.critical(self, "Compile Error", f"Failed to compile UI component on remote machine:\n{err}")
                return
            self.console.append("✅ UI Dummy Component compiled successfully.")
        
        remote_dir = remote_path.rsplit('/', 1)[0] if '/' in remote_path else '.'
        cmd = f"mkdir -p {remote_dir} && cat << 'EOF' > {remote_path}\n{hal_content}\nEOF"
        self.console.append(f"\n🚀 Deploying HAL to {remote_path} via SSH...")
        out, err = mesa_tab.execute_remote_command(cmd)
        
        if err and ("denied" in err.lower() or "no such file" in err.lower() or "cannot create" in err.lower()):
            QMessageBox.critical(self, "Deploy Failed", f"Error during deployment:\n{err}")
        else:
            self.console.append("✅ Deployment successful!")
            QMessageBox.information(self, "Success", f"Successfully deployed to:\n{remote_path}")

    def canvas_mouse_press(self, event):
        """Magnetic Wiring Logic injected into the Canvas."""
        # Route middle-click to canvas panning logic
        if event.button() == Qt.MouseButton.MiddleButton:
            MesaCanvas.mousePressEvent(self.canvas, event)
            return

        # Map the event position directly
        scene_pos = self.canvas.mapToScene(event.pos())
        
        # Check right click for wire deletion
        if event.button() == Qt.MouseButton.RightButton:
            if self.first_pin:
                # Safely cancel active routing if right-clicked
                self.first_pin.setBrush(getattr(self.first_pin, '_original_brush', QColor("#e1b12c")))
                self.first_pin = None
                
            items_at_click = self.canvas.scene.items(scene_pos)
            wire_to_del = next((i for i in items_at_click if isinstance(i, Wire)), None)
            if wire_to_del:
                self.save_undo_state()
                QTimer.singleShot(0, lambda w=wire_to_del: self._execute_wire_deletion(w))
                event.accept()
                return
                
            MesaCanvas.mousePressEvent(self.canvas, event)
            return
            
        items = self.canvas.scene.items(QRectF(scene_pos.x()-15, scene_pos.y()-15, 30, 30))
        pins = [i for i in items if isinstance(i, Pin)]
        
        if event.button() == Qt.MouseButton.LeftButton and pins:
            target_pin = pins[0]
            if not self.first_pin:
                self.first_pin = target_pin
                self.first_pin._original_brush = target_pin.brush() # Save the custom color
                target_pin.setBrush(QColor("#ffffff")) # Highlight
                self.console.append(f"Selected: {target_pin.label}. Choose a destination...")
            else:
                # Strict routing: Prevent Out->Out and In->In connections
                if target_pin != self.first_pin and target_pin.is_out != self.first_pin.is_out:
                    self.save_undo_state()
                    wire = Wire(self.first_pin, target_pin)
                    self.canvas.scene.addItem(wire)
                    self.console.append(f"Successfully connected {self.first_pin.label} to {target_pin.label}")
                else:
                    self.console.append("Failed: Must connect an Output to an Input.")
                self.first_pin.setBrush(getattr(self.first_pin, '_original_brush', QColor("#e1b12c"))) # Reset
                self.first_pin = None
            event.accept()
            return
            
        # Check left click for component movement (save state before move starts)
        if event.button() == Qt.MouseButton.LeftButton:
            items_at_click = self.canvas.scene.items(scene_pos)
            if any(isinstance(i, MesaComponent) for i in items_at_click):
                self.save_undo_state()

        # Pass to standard Qt behavior (move components, select bounding box)
        MesaCanvas.mousePressEvent(self.canvas, event)

    def canvas_mouse_double_click(self, event):
        scene_pos = self.canvas.mapToScene(event.pos())
        items_at_click = self.canvas.scene.items(scene_pos)
        wire_to_edit = next((i for i in items_at_click if isinstance(i, Wire)), None)
        
        if wire_to_edit:
            color = QColorDialog.getColor(wire_to_edit.base_color, self, "Select Wire Color")
            if color.isValid():
                self.save_undo_state()
                wire_to_edit.set_color(color.name())
            event.accept()
            return
            
        zone_to_edit = next((i for i in items_at_click if isinstance(i, ComponentZone)), None)
        if not zone_to_edit:
            zone_to_edit = next((i.parentItem() for i in items_at_click if isinstance(i.parentItem(), ComponentZone)), None)
            
        if zone_to_edit:
            new_title, ok = QInputDialog.getText(self, "Rename Zone", "Enter new zone name:", text=zone_to_edit.title.toPlainText())
            if ok and new_title.strip():
                self.save_undo_state()
                zone_to_edit.title.setPlainText(new_title.strip())
            event.accept()
            return
            
        MesaCanvas.mouseDoubleClickEvent(self.canvas, event)

    def _execute_wire_deletion(self, wire_to_del):
        grabber = self.canvas.scene.mouseGrabberItem()
        if grabber: grabber.ungrabMouse()
            
        if wire_to_del in getattr(wire_to_del.start_pin, 'wires', []): wire_to_del.start_pin.wires.remove(wire_to_del)
        if wire_to_del in getattr(wire_to_del.end_pin, 'wires', []): wire_to_del.end_pin.wires.remove(wire_to_del)
        if wire_to_del.scene(): 
            wire_to_del.ungrabMouse()
            wire_to_del.hide()
            self.canvas.scene.removeItem(wire_to_del)
        self.trash.append(wire_to_del)
        self.console.append("Deleted wire.")
        self.canvas.scene.update()

    def canvas_key_press(self, event):
        """Handles Copy, Paste, and Delete shortcuts for the canvas."""
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.copy_components()
            event.accept()
        elif event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.paste_components()
            event.accept()
        elif event.key() == Qt.Key.Key_Z:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.redo()
                else:
                    self.undo()
                event.accept()
            else:
                MesaCanvas.keyPressEvent(self.canvas, event)
        elif event.key() == Qt.Key.Key_Y and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.redo()
            event.accept()
        elif event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            self.delete_selected_components()
            event.accept()
        else:
            MesaCanvas.keyPressEvent(self.canvas, event)

    def copy_components(self):
        self.copied_components = []
        for item in self.canvas.scene.selectedItems():
            if isinstance(item, MesaComponent):
                self.copied_components.append({
                    "name": item.name,
                    "x": item.pos().x(),
                    "y": item.pos().y()
                })
        if self.copied_components:
            self.console.append(f"Copied {len(self.copied_components)} component(s).")

    def paste_components(self):
        if not hasattr(self, 'copied_components') or not self.copied_components:
            return
            
        self.save_undo_state()
        
        self.canvas.scene.clearSelection() # Deselect originals so pasted ones stand out
        
        for comp_data in self.copied_components:
            name = comp_data["name"]
            if name in self.library_tab.library_data:
                comp_id = f"{name}_{time.time_ns()}" # Generate fresh unique ID
                
                # Offset slightly so they don't spawn directly on top of each other
                new_x = comp_data["x"] + 30
                new_y = comp_data["y"] + 30
                
                comp = MesaComponent(name, new_x, new_y, self.library_tab.library_data[name], comp_id)
                self.canvas.scene.addItem(comp)
                comp.setSelected(True)
                
                # Update memory coordinates so rapid-pasting continues to shift them diagonally
                comp_data["x"] = new_x
                comp_data["y"] = new_y
                
        self.console.append(f"Pasted {len(self.copied_components)} component(s).")

    def delete_selected_components(self):
        items = list(self.canvas.scene.selectedItems())
        if not items:
            return
            
        self.save_undo_state()
        
        # Safely reset active routing to prevent C++ pointer crash
        if self.first_pin:
            try:
                self.first_pin.setBrush(getattr(self.first_pin, '_original_brush', QColor("#e1b12c")))
            except Exception:
                pass
            self.first_pin = None
        
        # Deselect all before removal to prevent selection-changed signals from crashing PyQt
        self.canvas.scene.clearSelection()
        
        wires_to_delete = set()
        comps_to_delete = set()
        
        for item in items:
            if isinstance(item, Wire):
                wires_to_delete.add(item)
            elif isinstance(item, MesaComponent):
                comps_to_delete.add(item)
                for pin in getattr(item, 'pins', []):
                    for wire in pin.wires:
                        wires_to_delete.add(wire)

        # Defer the physical removal to prevent C++ event loop crashes
        QTimer.singleShot(0, lambda: self._execute_deletion(wires_to_delete, comps_to_delete))

    def _execute_deletion(self, wires_to_delete, comps_to_delete):
        grabber = self.canvas.scene.mouseGrabberItem()
        if grabber: grabber.ungrabMouse()
            
        for wire in wires_to_delete:
            if hasattr(wire, 'start_pin') and wire in getattr(wire.start_pin, 'wires', []): wire.start_pin.wires.remove(wire)
            if hasattr(wire, 'end_pin') and wire in getattr(wire.end_pin, 'wires', []): wire.end_pin.wires.remove(wire)
            if wire.scene(): 
                wire.ungrabMouse()
                wire.hide()
                self.canvas.scene.removeItem(wire)

        for comp in comps_to_delete:
            if comp.scene(): 
                for child in comp.childItems(): child.ungrabMouse()
                comp.ungrabMouse()
                comp.hide()
                self.canvas.scene.removeItem(comp)

        # Send to trash to safely outlive the current event loop cycle!
        self.trash.extend(wires_to_delete)
        self.trash.extend(comps_to_delete)
        if len(self.trash) > 100:
            self.trash = self.trash[-50:]
            
        self.console.append(f"Deleted {len(comps_to_delete)} component(s) and {len(wires_to_delete)} wire(s).")
        self.canvas.scene.update()