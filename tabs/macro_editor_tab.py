import os
import subprocess
import tempfile
import math
import re
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QListWidget, QPlainTextEdit, QMessageBox, 
                             QInputDialog, QGraphicsView, QGraphicsScene, QSplitter, QLineEdit)
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath, QSyntaxHighlighter, QTextCharFormat, QFont

LOCAL_IPS = ['localhost', '127.0.0.1']
try:
    _out = subprocess.check_output("hostname -I", shell=True, text=True, stderr=subprocess.DEVNULL)
    LOCAL_IPS.extend(_out.split())
except:
    pass

def is_local_ip(ip):
    return ip in LOCAL_IPS

HAS_PARAMIKO = False
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    pass

class GCodeHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        
        # O-codes (Subroutine definitions)
        o_fmt = QTextCharFormat()
        o_fmt.setForeground(QColor("#f39c12"))
        o_fmt.setFontWeight(QFont.Weight.Bold)
        self.rules.append((re.compile(r"[Oo]\s*<.*?>|[Oo]\s*\d+"), o_fmt))

        # Control Words (sub, endsub, call, return)
        ctrl_fmt = QTextCharFormat()
        ctrl_fmt.setForeground(QColor("#e67e22"))
        ctrl_fmt.setFontWeight(QFont.Weight.Bold)
        self.rules.append((re.compile(r"\b(sub|endsub|call|return|if|else|endif|while|endwhile|do)\b", re.IGNORECASE), ctrl_fmt))

        # Variables (#1, #5420, #<test>)
        var_fmt = QTextCharFormat()
        var_fmt.setForeground(QColor("#9b59b6"))
        self.rules.append((re.compile(r"#\s*<\w+>|#\s*\d+"), var_fmt))

        # G and M Codes
        g_fmt = QTextCharFormat()
        g_fmt.setForeground(QColor("#3498db"))
        self.rules.append((re.compile(r"[GgMm]\s*\d+(\.\d+)?"), g_fmt))

        # Coordinates
        coord_fmt = QTextCharFormat()
        coord_fmt.setForeground(QColor("#2ecc71"))
        self.rules.append((re.compile(r"[XxYyZzIiJjKkAabBcC]\s*-?\d+(\.\d+)?"), coord_fmt))

        # Comments
        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor("#7f8c8d"))
        comment_fmt.setFontItalic(True)
        self.rules.append((re.compile(r";.*"), comment_fmt))
        self.rules.append((re.compile(r"\(.*?\)"), comment_fmt))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            for match in pattern.finditer(text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)

class IsoViewer(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setStyleSheet("background-color: #111; border: 1px solid #444; border-radius: 4px;")
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        
    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            event.ignore()
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self.scale(factor, factor)
        event.accept()
        
    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        painter.save()
        painter.resetTransform()
        
        view_rect = self.viewport().rect()
        margin_x = 40
        margin_y = 40
        length = 40
        
        ox = view_rect.left() + margin_x
        oy = view_rect.bottom() - margin_y
        
        font = QFont("Arial", 10, QFont.Weight.Bold)
        painter.setFont(font)
        
        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))
        
        def to_iso_2d(dx, dy, dz):
            return ox + (dx - dy) * cos_a, oy + (-dz + (dx + dy) * sin_a)
            
        end_x = to_iso_2d(length, 0, 0)
        painter.setPen(QPen(QColor(255, 50, 50, 200), 3))
        painter.drawLine(int(ox), int(oy), int(end_x[0]), int(end_x[1]))
        painter.setPen(QColor(255, 50, 50, 200))
        painter.drawText(int(end_x[0] + 5), int(end_x[1] + 5), "X")
        
        end_y = to_iso_2d(0, length, 0)
        painter.setPen(QPen(QColor(50, 255, 50, 200), 3))
        painter.drawLine(int(ox), int(oy), int(end_y[0]), int(end_y[1]))
        painter.setPen(QColor(50, 255, 50, 200))
        painter.drawText(int(end_y[0] - 15), int(end_y[1] + 5), "Y")
        
        end_z = to_iso_2d(0, 0, length)
        painter.setPen(QPen(QColor(50, 150, 255, 200), 3))
        painter.drawLine(int(ox), int(oy), int(end_z[0]), int(end_z[1]))
        painter.setPen(QColor(50, 150, 255, 200))
        painter.drawText(int(end_z[0] - 5), int(end_z[1] - 8), "Z")
        
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(int(ox) - 3, int(oy) - 3, 6, 6)
        
        painter.restore()

    def render_isometric(self, text):
        self.scene.clear()
        
        # Iso transform constants (30 degrees)
        cos_a = math.cos(math.radians(30))
        sin_a = math.sin(math.radians(30))
        
        def to_iso(x, y, z):
            # Standard Isometric Projection mapping
            iso_x = (x - y) * cos_a
            iso_y = -z + (x + y) * sin_a
            return iso_x, iso_y
            
        feed_pen = QPen(QColor("#39FF14"), 1.5)
        feed_pen.setCosmetic(True)
        rapid_pen = QPen(QColor(150, 150, 150, 100), 1, Qt.PenStyle.DashLine)
        rapid_pen.setCosmetic(True)
        
        x, y, z = 0.0, 0.0, 0.0
        current_mode = 'G1'
        
        for line in text.splitlines():
            clean_line = line.split(';')[0].split('(')[0].strip().upper()
            if not clean_line: continue
                
            tokens = clean_line.split()
            for token in tokens:
                if token in ['G0', 'G00']: current_mode = 'G0'
                elif token in ['G1', 'G01', 'G38.2', 'G38.3', 'G38.4', 'G38.5']: current_mode = 'G1'
                
            new_x, new_y, new_z = x, y, z
            has_move = False
            
            for token in tokens:
                # Very basic parsing, ignores variables like #1
                try:
                    if token.startswith('X') and not '#' in token:
                        new_x = float(token[1:]); has_move = True
                    elif token.startswith('Y') and not '#' in token:
                        new_y = float(token[1:]); has_move = True
                    elif token.startswith('Z') and not '#' in token:
                        new_z = float(token[1:]); has_move = True
                except: pass
                
            if has_move:
                iso_start = to_iso(x, y, z)
                iso_end = to_iso(new_x, new_y, new_z)
                
                pen = rapid_pen if current_mode == 'G0' else feed_pen
                self.scene.addLine(iso_start[0], iso_start[1], iso_end[0], iso_end[1], pen)
                
                x, y, z = new_x, new_y, new_z
                
        rect = self.scene.itemsBoundingRect()
        if rect.width() > 0 and rect.height() > 0:
            # Add padding by scaling the rect out slightly before fitting
            padded_rect = rect.adjusted(-rect.width()*0.05, -rect.height()*0.05, rect.width()*0.05, rect.height()*0.05)
            self.fitInView(padded_rect, Qt.AspectRatioMode.KeepAspectRatio)

class MacroEditorTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.current_file = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet("""
            QWidget { background-color: #1e1e1e; color: #ffffff; font-family: 'Segoe UI', Arial, sans-serif; }
            QPushButton { background-color: #2980b9; color: white; font-weight: bold; border-radius: 4px; padding: 6px 12px; border: none; }
            QPushButton:hover { background-color: #3498db; }
            QPushButton:pressed { background-color: #1f618d; }
            QListWidget { background-color: #252526; color: #ffffff; border: 1px solid #444; font-size: 14px; }
            QListWidget::item:selected { background-color: #2980b9; color: #ffffff; }
            QLineEdit { background-color: #2d2d30; color: white; border: 1px solid #555; padding: 6px; border-radius: 4px; }
        """)

        # Top Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Remote Macro Directory:"))
        
        default_macro_dir = self.settings.get_setting("PATHS", "last_macro_dir", "~/linuxcnc/nc_files")
        self.path_input = QLineEdit(default_macro_dir)
        toolbar.addWidget(self.path_input)
        
        btn_refresh = QPushButton("🔄 Refresh List")
        btn_refresh.clicked.connect(self.refresh_file_list)
        toolbar.addWidget(btn_refresh)
        
        btn_new = QPushButton("➕ New Macro")
        btn_new.setStyleSheet("background-color: #27ae60;")
        btn_new.clicked.connect(self.new_macro)
        toolbar.addWidget(btn_new)
        
        layout.addLayout(toolbar)

        # Main Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left: File List
        self.file_list = QListWidget()
        self.file_list.itemClicked.connect(self.load_file)
        splitter.addWidget(self.file_list)
        
        # Middle: Editor
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        
        editor_toolbar = QHBoxLayout()
        self.lbl_current_file = QLabel("No File Selected")
        self.lbl_current_file.setStyleSheet("color: #f39c12; font-weight: bold;")
        editor_toolbar.addWidget(self.lbl_current_file)
        editor_toolbar.addStretch()
        
        self.btn_save = QPushButton("💾 Save to LinuxCNC")
        self.btn_save.setStyleSheet("background-color: #8e44ad;")
        self.btn_save.clicked.connect(self.save_file)
        editor_toolbar.addWidget(self.btn_save)
        editor_layout.addLayout(editor_toolbar)
        
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet("background-color: #1a1a1a; color: #ecf0f1; font-family: 'Consolas', 'Courier New', monospace; font-size: 14px; border: 1px solid #555;")
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = GCodeHighlighter(self.editor.document())
        self.editor.textChanged.connect(self.update_preview)
        self.editor.textChanged.connect(self.mark_unsaved)
        editor_layout.addWidget(self.editor)
        
        splitter.addWidget(editor_widget)
        
        # Right: Iso Preview
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        preview_lbl = QLabel("3D Isometric Preview")
        preview_lbl.setStyleSheet("font-weight: bold; color: #ccc;")
        preview_layout.addWidget(preview_lbl)
        
        self.viewer = IsoViewer()
        preview_layout.addWidget(self.viewer)
        
        info_lbl = QLabel("Note: Variables (e.g. #1) are ignored in preview.")
        info_lbl.setStyleSheet("color: #777; font-style: italic; font-size: 11px;")
        preview_layout.addWidget(info_lbl)
        
        splitter.addWidget(preview_widget)
        
        splitter.setSizes([200, 500, 300])
        layout.addWidget(splitter)

    def get_ssh_creds(self):
        ip = self.settings.get_setting("SSH", "ip_address", "")
        user = self.settings.get_setting("SSH", "username", "")
        password = self.settings.get_setting("SSH", "password", "")
        return ip, user, password

    def refresh_file_list(self):
        ip, user, password = self.get_ssh_creds()
        if not ip or not user:
            QMessageBox.warning(self, "No SSH", "Please configure SSH credentials in Settings.")
            return
            
        remote_dir = self.path_input.text().strip().rstrip('/')
        self.settings.save_setting("PATHS", "last_macro_dir", remote_dir)
        
        cmd = f"cd {remote_dir} && find -L . -type f -name '*.ngc' 2>/dev/null | sed 's|^./||'"
        
        self.file_list.clear()
        
        try:
            out = ""
            if is_local_ip(ip):
                expanded_dir = os.path.expanduser(remote_dir)
                if os.path.exists(expanded_dir):
                    file_list = []
                    for root, dirs, files in os.walk(expanded_dir, followlinks=True):
                        for f in files:
                            if f.endswith('.ngc'):
                                rel = os.path.relpath(os.path.join(root, f), expanded_dir)
                                file_list.append(rel.replace('\\', '/'))
                    out = "\n".join(file_list)
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                out = stdout.read().decode().strip()
                ssh.close()
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=5)
                out = res.stdout.strip()
                
            if out:
                for line in out.splitlines():
                    filename = line.strip()
                    if filename:
                        self.file_list.addItem(filename)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to list directory:\n{e}")

    def new_macro(self):
        filename, ok = QInputDialog.getText(self, "New Macro", "Enter filename (e.g. subfolder/tool_probe.ngc):")
        if ok and filename:
            if not filename.endswith(".ngc"): filename += ".ngc"
            self.current_file = filename
            self.lbl_current_file.setText(f"Editing: {filename}")
            self.editor.clear()
            base_name = os.path.basename(filename).replace('.ngc', '')
            self.editor.appendPlainText(f"O<{base_name}> sub\n\n( Your macro logic here )\n\nO<{base_name}> endsub\nM2")
            self.mark_unsaved()

    def load_file(self, item):
        if getattr(self, 'has_unsaved_changes', False):
            reply = QMessageBox.question(self, "Unsaved Changes", "You have unsaved changes. Discard them?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes: return
            
        filename = item.text()
        remote_dir = self.path_input.text().strip().rstrip('/')
        remote_path = f"{remote_dir}/{filename}"
        
        ip, user, password = self.get_ssh_creds()
        cmd = f"cat {remote_path}"
        
        try:
            out = ""
            if is_local_ip(ip):
                expanded = os.path.expanduser(remote_path)
                with open(expanded, 'r') as f: out = f.read()
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                stdin, stdout, stderr = ssh.exec_command(cmd)
                out = stdout.read().decode()
                ssh.close()
            else:
                res = subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=5)
                out = res.stdout
                
            self._loading = True
            self.editor.setPlainText(out)
            self.current_file = filename
            self.lbl_current_file.setText(f"Editing: {filename}")
            self.clear_unsaved()
            self._loading = False
            self.update_preview()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file:\n{e}")

    def save_file(self):
        if not self.current_file: return
        
        remote_dir = self.path_input.text().strip().rstrip('/')
        remote_path = f"{remote_dir}/{self.current_file}"
        content = self.editor.toPlainText()
        
        ip, user, password = self.get_ssh_creds()
        
        remote_parent = remote_path.rsplit('/', 1)[0]
        try:
            if is_local_ip(ip):
                expanded = os.path.expanduser(remote_path)
                os.makedirs(os.path.dirname(expanded), exist_ok=True)
                with open(expanded, 'w') as f: f.write(content)
            elif HAS_PARAMIKO:
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ip, username=user, password=password, timeout=5, look_for_keys=not bool(password), allow_agent=not bool(password))
                ssh.exec_command(f"mkdir -p \"{remote_parent}\"")
                sftp = ssh.open_sftp()
                with sftp.file(remote_path, 'w') as f: f.write(content.encode('utf-8'))
                sftp.close()
                ssh.close()
            else:
                with tempfile.NamedTemporaryFile(delete=False, mode='w') as f:
                    f.write(content)
                    temp_path = f.name
                subprocess.run(["ssh", "-o", "BatchMode=yes", f"{user}@{ip}", f"mkdir -p \"{remote_parent}\""], capture_output=True)
                subprocess.run(["scp", "-o", "BatchMode=yes", temp_path, f"{user}@{ip}:{remote_path}"], capture_output=True)
                os.remove(temp_path)
                
            self.clear_unsaved()
            QMessageBox.information(self, "Success", f"Saved to {remote_path}")
            if self.file_list.findItems(self.current_file, Qt.MatchFlag.MatchExactly) == []:
                self.refresh_file_list()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save file:\n{e}")

    def mark_unsaved(self):
        if getattr(self, '_loading', False): return
        self.has_unsaved_changes = True
        self.btn_save.setText("💾 Save to LinuxCNC (Unsaved*)")
        self.btn_save.setStyleSheet("background-color: #e74c3c; border: 2px solid #c0392b;")

    def clear_unsaved(self):
        self.has_unsaved_changes = False
        self.btn_save.setText("💾 Save to LinuxCNC")
        self.btn_save.setStyleSheet("background-color: #8e44ad;")

    def update_preview(self):
        self.viewer.render_isometric(self.editor.toPlainText())