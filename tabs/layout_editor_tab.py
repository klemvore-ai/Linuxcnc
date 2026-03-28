from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
                             QListWidget, QGraphicsView, QGraphicsScene, QGraphicsRectItem, 
                             QGraphicsTextItem, QInputDialog, QFileDialog, QMessageBox, QCheckBox,
                             QGroupBox, QGridLayout, QDialog, QFormLayout, QComboBox, QColorDialog, QLineEdit, QMenu, QFontComboBox, QSpinBox, QGraphicsDropShadowEffect)
from PyQt6.QtCore import Qt, QRectF, QPointF, QUrl, QTimer
from PyQt6.QtGui import QColor, QPen, QBrush, QFont, QPainter, QPolygonF, QShortcut, QKeySequence, QDesktopServices, QCursor, QPixmap
import time
import json
import os
import shutil

class UIBlock(QGraphicsRectItem):
    def __init__(self, widget_type, title, x, y, save_state_cb=None):
        super().__init__(0, 0, 150, 60)
        self.setPos(x, y)
        self.widget_type = widget_type
        self.save_state_cb = save_state_cb
        
        self.setFlags(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable | 
                      QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable |
                      QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges)
                      
        self.setAcceptHoverEvents(True)
        self._is_resizing = False
        self.is_locked = False
        
        # Styling based on type
        color = QColor("#34495e")
        if widget_type == "Button": color = QColor("#2980b9")
        elif widget_type == "DRO": color = QColor("#27ae60")
        elif widget_type == "G-Code Editor": color = QColor("#8e44ad")
        elif widget_type == "3D Viewer": color = QColor("#d35400")
        
        self.bg_color = color.name()
        self.text_color = "#ffffff"
        self.alignment = "Center"
        self.valignment = "Center"
        self.image_path = ""
        self.show_text = True
        self.image_scaling = "Stretch"
        self.font_family = "System Default"
        self.font_size = 10
        self.border_color = "#2c3e50"
        self.border_width = 2
        self.border_radius = 10 if widget_type == "LED" else 5
        self.has_shadow = False
        
        self.setPen(QPen(QColor(self.border_color), self.border_width))
        
        self.label = QGraphicsTextItem(title, self)
        
        self.update_visuals()

    def set_locked(self, locked):
        self.is_locked = locked
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, not locked)
        self.update()

    def update_visuals(self):
        if self.image_path and os.path.exists(self.image_path):
            aspect_mode = Qt.AspectRatioMode.IgnoreAspectRatio
            if self.image_scaling == "Fit":
                aspect_mode = Qt.AspectRatioMode.KeepAspectRatio
            elif self.image_scaling == "Crop":
                aspect_mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding
                
            pixmap = QPixmap(self.image_path).scaled(int(self.rect().width()), int(self.rect().height()), aspect_mode, Qt.TransformationMode.SmoothTransformation)
            self.setBrush(QBrush(pixmap))
        else:
            self.setBrush(QBrush(QColor(self.bg_color)))
            
        self.label.setVisible(self.show_text)
        self.label.setDefaultTextColor(QColor(self.text_color))
        
        sys_font = QFont()
        if hasattr(self, 'font_family') and self.font_family and self.font_family != "System Default":
            sys_font.setFamily(self.font_family)
        sys_font.setPointSize(int(getattr(self, 'font_size', 10)))
        sys_font.setWeight(QFont.Weight.Bold)
        self.label.setFont(sys_font)
        
        self.update_alignment()
        
        if getattr(self, 'has_shadow', False):
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(15)
            shadow.setOffset(3, 3)
            shadow.setColor(QColor(0, 0, 0, 150))
            self.setGraphicsEffect(shadow)
        else:
            self.setGraphicsEffect(None)

    def update_alignment(self):
        rect = self.rect()
        text_rect = self.label.boundingRect()
        
        if self.alignment == "Left":
            x = 10
        elif self.alignment == "Center":
            x = (rect.width() - text_rect.width()) / 2
        elif self.alignment == "Right":
            x = rect.width() - text_rect.width() - 10
        else:
            x = (rect.width() - text_rect.width()) / 2
            
        valign = getattr(self, 'valignment', 'Center')
        if valign == "Top":
            y = 5
        elif valign == "Center":
            y = (rect.height() - text_rect.height()) / 2
        elif valign == "Bottom":
            y = rect.height() - text_rect.height() - 5
        else:
            y = (rect.height() - text_rect.height()) / 2
            
        self.label.setPos(x, y)

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.GraphicsItemChange.ItemPositionChange:
            # Snap to grid logic (20px)
            new_pos = value
            x = round(new_pos.x() / 20.0) * 20.0
            y = round(new_pos.y() / 20.0) * 20.0
            return QPointF(x, y)
        return super().itemChange(change, value)
        
    def paint(self, painter, option, widget=None):
        if self.isSelected():
            self.setPen(QPen(QColor("#f1c40f"), 3, Qt.PenStyle.DashLine))
        else:
            self.setPen(QPen(QColor(getattr(self, 'border_color', '#2c3e50')), getattr(self, 'border_width', 2)))
        super().paint(painter, option, widget)
        
        # Draw a visual resize handle (triangle) in the bottom right corner
        if not getattr(self, 'is_locked', False):
            rect = self.rect()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 255, 255, 80)))
            poly = QPolygonF([
                QPointF(rect.width(), rect.height() - 15), 
                QPointF(rect.width(), rect.height()), 
                QPointF(rect.width() - 15, rect.height())
            ])
            painter.drawPolygon(poly)

    def hoverMoveEvent(self, event):
        rect = self.rect()
        resize_handle = QRectF(rect.width() - 15, rect.height() - 15, 15, 15)
        if resize_handle.contains(event.pos()) and not getattr(self, 'is_locked', False):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        # Save state before moving or resizing begins
        if event.button() == Qt.MouseButton.LeftButton and self.save_state_cb:
            self.save_state_cb()
            
        rect = self.rect()
        resize_handle = QRectF(rect.width() - 15, rect.height() - 15, 15, 15)
        if event.button() == Qt.MouseButton.LeftButton and resize_handle.contains(event.pos()) and not getattr(self, 'is_locked', False):
            self._is_resizing = True
            self._resize_start_pos = event.scenePos()
            self._resize_start_rect = self.rect()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_resizing:
            delta = event.scenePos() - self._resize_start_pos
            
            # Calculate new size and clamp to a minimum width/height
            new_w = max(40.0, self._resize_start_rect.width() + delta.x())
            new_h = max(40.0, self._resize_start_rect.height() + delta.y())
            
            # Snap resizing to the 20px grid
            new_w = round(new_w / 20.0) * 20.0
            new_h = round(new_h / 20.0) * 20.0
            
            self.setRect(0, 0, new_w, new_h)
            self.update_visuals()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_resizing:
            self._is_resizing = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

class PropertyEditorDialog(QDialog):
    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.block = block
        self.setWindowTitle(f"Edit {block.widget_type} Properties")
        self.layout = QFormLayout(self)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; color: #ffffff; }
            QLabel { font-weight: bold; color: #ecf0f1; }
            QLineEdit, QComboBox { background-color: #2d2d30; color: white; border: 1px solid #555555; padding: 4px; border-radius: 4px; }
            QPushButton { background-color: #2980b9; color: white; border-radius: 4px; padding: 4px 8px; }
        """)
        
        self.title_input = QLineEdit(block.label.toPlainText())
        self.layout.addRow("Title/Identifier:", self.title_input)
        
        self.show_text_chk = QCheckBox("Show Title Text")
        self.show_text_chk.setChecked(block.show_text)
        self.layout.addRow("", self.show_text_chk)
        
        self.has_shadow_chk = QCheckBox("Enable Drop Shadow")
        self.has_shadow_chk.setChecked(getattr(block, 'has_shadow', False))
        self.layout.addRow("", self.has_shadow_chk)
        
        self.font_cb = QFontComboBox()
        if hasattr(block, 'font_family') and block.font_family and block.font_family != "System Default":
            self.font_cb.setCurrentFont(QFont(block.font_family))
        self.layout.addRow("Font Family:", self.font_cb)
        
        self.font_size_sb = QSpinBox()
        self.font_size_sb.setRange(6, 144)
        self.font_size_sb.setValue(int(getattr(block, 'font_size', 10)))
        self.layout.addRow("Font Size (pt):", self.font_size_sb)
        
        self.border_color_btn = QPushButton("Choose Color")
        self.border_color = getattr(block, 'border_color', '#2c3e50')
        self.border_color_btn.setStyleSheet(f"background-color: {self.border_color}; color: {'#000' if QColor(self.border_color).lightness() > 128 else '#fff'};")
        self.border_color_btn.clicked.connect(self.choose_border_color)
        self.layout.addRow("Border Color:", self.border_color_btn)
        
        self.border_width_sb = QSpinBox()
        self.border_width_sb.setRange(0, 50)
        self.border_width_sb.setValue(int(getattr(block, 'border_width', 2)))
        self.layout.addRow("Border Width (px):", self.border_width_sb)
        
        self.border_radius_sb = QSpinBox()
        self.border_radius_sb.setRange(0, 100)
        self.border_radius_sb.setValue(int(getattr(block, 'border_radius', 5)))
        self.layout.addRow("Border Radius (px):", self.border_radius_sb)
        
        self.bg_color_btn = QPushButton("Choose Color")
        self.bg_color_btn.setStyleSheet(f"background-color: {block.bg_color}; color: {'#000' if QColor(block.bg_color).lightness() > 128 else '#fff'};")
        self.bg_color = block.bg_color
        self.bg_color_btn.clicked.connect(self.choose_bg_color)
        self.layout.addRow("Background Color:", self.bg_color_btn)
        
        self.text_color_btn = QPushButton("Choose Color")
        self.text_color_btn.setStyleSheet(f"background-color: {block.text_color}; color: {'#000' if QColor(block.text_color).lightness() > 128 else '#fff'};")
        self.text_color = block.text_color
        self.text_color_btn.clicked.connect(self.choose_text_color)
        self.layout.addRow("Text Color:", self.text_color_btn)
        
        self.align_cb = QComboBox()
        self.align_cb.addItems(["Left", "Center", "Right"])
        self.align_cb.setCurrentText(block.alignment)
        self.layout.addRow("Horiz. Alignment:", self.align_cb)
        
        self.valign_cb = QComboBox()
        self.valign_cb.addItems(["Top", "Center", "Bottom"])
        self.valign_cb.setCurrentText(getattr(block, 'valignment', 'Center'))
        self.layout.addRow("Vert. Alignment:", self.valign_cb)
        
        self.img_input = QLineEdit(block.image_path)
        self.img_btn = QPushButton("Browse Image")
        self.img_btn.clicked.connect(self.choose_image)
        img_layout = QHBoxLayout()
        img_layout.addWidget(self.img_input)
        img_layout.addWidget(self.img_btn)
        self.layout.addRow("Background Image:", img_layout)
        
        self.img_scale_cb = QComboBox()
        self.img_scale_cb.addItems(["Stretch", "Fit", "Crop"])
        self.img_scale_cb.setCurrentText(block.image_scaling)
        self.layout.addRow("Image Scaling:", self.img_scale_cb)
        
        save_btn = QPushButton("💾 Apply Properties")
        save_btn.setStyleSheet("background-color: #27ae60; font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(self.accept)
        self.layout.addRow(save_btn)
        
    def choose_bg_color(self):
        color = QColorDialog.getColor(QColor(self.bg_color), self, "Select Background Color")
        if color.isValid():
            self.bg_color = color.name()
            self.bg_color_btn.setStyleSheet(f"background-color: {self.bg_color}; color: {'#000' if color.lightness() > 128 else '#fff'};")
            
    def choose_text_color(self):
        color = QColorDialog.getColor(QColor(self.text_color), self, "Select Text Color")
        if color.isValid():
            self.text_color = color.name()
            self.text_color_btn.setStyleSheet(f"background-color: {self.text_color}; color: {'#000' if color.lightness() > 128 else '#fff'};")
            
    def choose_border_color(self):
        color = QColorDialog.getColor(QColor(self.border_color), self, "Select Border Color")
        if color.isValid():
            self.border_color = color.name()
            self.border_color_btn.setStyleSheet(f"background-color: {self.border_color}; color: {'#000' if color.lightness() > 128 else '#fff'};")
            
    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Image", "", "Images (*.png *.jpg *.jpeg *.svg)")
        if path:
            self.img_input.setText(path)

class LayoutCanvas(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.setSceneRect(0, 0, 2000, 2000)
        
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#1e1e1e")))
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        
        self.setStyleSheet("QRubberBand { background-color: rgba(41, 128, 185, 80); border: 2px solid #3498db; }")

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        # Draw layout grid
        painter.setPen(QPen(QColor("#2c3e50"), 1, Qt.PenStyle.DotLine))
        grid_size = 20
        left = int(rect.left()) - (int(rect.left()) % grid_size)
        top = int(rect.top()) - (int(rect.top()) % grid_size)
        for x in range(left, int(rect.right()) + grid_size, grid_size):
            painter.drawLine(x, int(rect.top()), x, int(rect.bottom()))
        for y in range(top, int(rect.bottom()) + grid_size, grid_size):
            painter.drawLine(int(rect.left()), y, int(rect.right()), y)

class LayoutEditorTab(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.settings = parent_app.settings
        self.undo_stack = []
        self.redo_stack = []
        self._is_restoring = False
        self.has_unsaved_changes = False
        self.init_ui()
        
        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.timeout.connect(self.auto_save)
        interval_minutes = int(self.settings.get_setting("UI_PREFERENCES", "auto_save_interval_minutes", 5))
        self.auto_save_timer.start(interval_minutes * 60000)
        
        # Check for recovery file after a short delay to allow the UI to show first
        QTimer.singleShot(200, self.check_recovery)

    def init_ui(self):
        layout = QHBoxLayout(self)
        
        # Toolbox Sidebar
        sidebar = QVBoxLayout()
        sidebar.addWidget(QLabel("<b>UI Toolbox</b>"))
        
        self.toolbox = QListWidget()
        self.toolbox.addItems([
            "Button (Macro/Command)", 
            "Toggle Button (State)",
            "DRO (Position/Value)", 
            "LED Indicator",
            "G-Code Editor", 
            "3D Viewer (Toolpath)",
            "Tab Container"
        ])
        sidebar.addWidget(self.toolbox)
        
        btn_add = QPushButton("📥 Place Component")
        btn_add.setStyleSheet("background: #27ae60; color: white; padding: 8px; font-weight: bold;")
        btn_add.clicked.connect(self.add_component)
        sidebar.addWidget(btn_add)
        
        btn_rem = QPushButton("➖ Remove Selected")
        btn_rem.clicked.connect(self.remove_selected)
        sidebar.addWidget(btn_rem)
        
        # Undo / Redo Row
        undo_layout = QHBoxLayout()
        btn_undo = QPushButton("↶ Undo")
        btn_undo.clicked.connect(self.undo)
        btn_redo = QPushButton("↷ Redo")
        btn_redo.clicked.connect(self.redo)
        undo_layout.addWidget(btn_undo)
        undo_layout.addWidget(btn_redo)
        sidebar.addLayout(undo_layout)
        
        # Alignment Toolbox
        align_group = QGroupBox("Align Selected")
        align_group.setStyleSheet("QGroupBox { font-weight: bold; color: #ecf0f1; border: 1px solid #444; border-radius: 4px; margin-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        align_layout = QGridLayout()
        
        btn_al_l = QPushButton("⇤ Left")
        btn_al_c = QPushButton("⇹ Center X")
        btn_al_r = QPushButton("⇥ Right")
        btn_al_t = QPushButton("⇤ Top")
        btn_al_m = QPushButton("⇕ Center Y")
        btn_al_b = QPushButton("⇥ Bottom")
        
        btn_al_l.clicked.connect(lambda: self.align_selected("left"))
        btn_al_c.clicked.connect(lambda: self.align_selected("center_x"))
        btn_al_r.clicked.connect(lambda: self.align_selected("right"))
        btn_al_t.clicked.connect(lambda: self.align_selected("top"))
        btn_al_m.clicked.connect(lambda: self.align_selected("center_y"))
        btn_al_b.clicked.connect(lambda: self.align_selected("bottom"))
        
        align_layout.addWidget(btn_al_l, 0, 0)
        align_layout.addWidget(btn_al_c, 0, 1)
        align_layout.addWidget(btn_al_r, 0, 2)
        align_layout.addWidget(btn_al_t, 1, 0)
        align_layout.addWidget(btn_al_m, 1, 1)
        align_layout.addWidget(btn_al_b, 1, 2)
        align_group.setLayout(align_layout)
        sidebar.addWidget(align_group)
        
        sidebar.addSpacing(20)
        
        btn_load = QPushButton("📂 Load UI Layout")
        btn_load.setStyleSheet("background: #2980b9; color: white; padding: 8px; font-weight: bold;")
        btn_load.clicked.connect(self.load_layout)
        sidebar.addWidget(btn_load)
        
        btn_save = QPushButton("💾 Save UI Layout")
        btn_save.setStyleSheet("background: #8e44ad; color: white; padding: 8px; font-weight: bold;")
        btn_save.clicked.connect(self.save_layout)
        sidebar.addWidget(btn_save)
        
        btn_export = QPushButton("📤 Export for Sharing")
        btn_export.setStyleSheet("background: #d35400; color: white; padding: 8px; font-weight: bold;")
        btn_export.clicked.connect(self.export_for_sharing)
        sidebar.addWidget(btn_export)
        
        sidebar.addStretch()
        layout.addLayout(sidebar, 1)
        
        # Designer Canvas
        self.canvas = LayoutCanvas()
        self.canvas.mouseDoubleClickEvent = self.canvas_mouse_double_click
        self.canvas.mousePressEvent = self.canvas_mouse_press
        layout.addWidget(self.canvas, 5)

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self.redo)
        
        shortcut_del = QShortcut(QKeySequence.StandardKey.Delete, self)
        shortcut_del.activated.connect(self.remove_selected)
        shortcut_back = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self)
        shortcut_back.activated.connect(self.remove_selected)
        
        shortcut_up = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        shortcut_up.activated.connect(lambda: self.nudge_selected(0, -20))
        shortcut_down = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        shortcut_down.activated.connect(lambda: self.nudge_selected(0, 20))
        shortcut_left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        shortcut_left.activated.connect(lambda: self.nudge_selected(-20, 0))
        shortcut_right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        shortcut_right.activated.connect(lambda: self.nudge_selected(20, 0))
        
        shortcut_sel_all = QShortcut(QKeySequence("Ctrl+A"), self)
        shortcut_sel_all.activated.connect(self.select_all)

    def open_property_dialog(self, block_to_edit):
        dlg = PropertyEditorDialog(block_to_edit, self)
        if dlg.exec():
            self.save_undo_state()
            
            # Apply new properties
            block_to_edit.label.setPlainText(dlg.title_input.text())
            block_to_edit.bg_color = dlg.bg_color
            block_to_edit.text_color = dlg.text_color
            block_to_edit.alignment = dlg.align_cb.currentText()
            block_to_edit.valignment = dlg.valign_cb.currentText()
            block_to_edit.font_family = dlg.font_cb.currentFont().family()
            block_to_edit.font_size = dlg.font_size_sb.value()
            block_to_edit.border_color = dlg.border_color
            block_to_edit.border_width = dlg.border_width_sb.value()
            block_to_edit.border_radius = dlg.border_radius_sb.value()
            block_to_edit.image_path = dlg.img_input.text()
            block_to_edit.show_text = dlg.show_text_chk.isChecked()
            block_to_edit.has_shadow = dlg.has_shadow_chk.isChecked()
            block_to_edit.image_scaling = dlg.img_scale_cb.currentText()
            
            block_to_edit.update_visuals()
            self.canvas.scene.update()

    def canvas_mouse_double_click(self, event):
        scene_pos = self.canvas.mapToScene(event.pos())
        items_at_click = self.canvas.scene.items(scene_pos)
        block_to_edit = next((i for i in items_at_click if isinstance(i, UIBlock)), None)
        
        if block_to_edit:
            self.open_property_dialog(block_to_edit)
            event.accept()
            return
            
        QGraphicsView.mouseDoubleClickEvent(self.canvas, event)

    def canvas_mouse_press(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            scene_pos = self.canvas.mapToScene(event.pos())
            items_at_click = self.canvas.scene.items(scene_pos)
            block_to_edit = next((i for i in items_at_click if isinstance(i, UIBlock)), None)
            
            if block_to_edit:
                menu = QMenu(self)
                menu.setStyleSheet("QMenu { background-color: #2d2d30; color: white; border: 1px solid #555555; } QMenu::item:selected { background-color: #2980b9; }")
                
                # Capture active group selection
                selected_blocks = [item for item in self.canvas.scene.selectedItems() if isinstance(item, UIBlock)]
                if block_to_edit not in selected_blocks:
                    self.canvas.scene.clearSelection()
                    block_to_edit.setSelected(True)
                    selected_blocks = [block_to_edit]
                    
                is_multi = len(selected_blocks) > 1

                edit_action = menu.addAction("✏️ Edit Properties")
                if is_multi: edit_action.setEnabled(False) # Properties are 1-to-1
                
                dup_action = menu.addAction("👯 Duplicate Selected" if is_multi else "👯 Duplicate Component")
                front_action = menu.addAction("⏫ Bring Selected to Front" if is_multi else "⏫ Bring to Front")
                back_action = menu.addAction("⏬ Send Selected to Back" if is_multi else "⏬ Send to Back")
                
                target_locked = not block_to_edit.is_locked
                lock_text = ("🔒 Lock" if target_locked else "🔓 Unlock") + (" Selected" if is_multi else " Component")
                lock_action = menu.addAction(lock_text)
                
                del_action = menu.addAction("🗑️ Delete Selected" if is_multi else "🗑️ Delete Component")
                
                if all(getattr(b, 'is_locked', False) for b in selected_blocks):
                    del_action.setEnabled(False)
                
                action = menu.exec(QCursor.pos())
                if action == edit_action:
                    self.open_property_dialog(block_to_edit)
                elif action == dup_action:
                    self.save_undo_state()
                    for b in selected_blocks:
                        self.duplicate_block(b, save_undo=False)
                elif action == front_action:
                    self.save_undo_state()
                    max_z = max([i.zValue() for i in self.canvas.scene.items()] + [0.0])
                    for b in selected_blocks:
                        max_z += 1
                        b.setZValue(max_z)
                    self.has_unsaved_changes = True
                elif action == back_action:
                    self.save_undo_state()
                    min_z = min([i.zValue() for i in self.canvas.scene.items()] + [0.0])
                    for b in selected_blocks:
                        min_z -= 1
                        b.setZValue(min_z)
                    self.has_unsaved_changes = True
                elif action == lock_action:
                    self.save_undo_state()
                    for b in selected_blocks:
                        b.set_locked(target_locked)
                    self.has_unsaved_changes = True
                elif action == del_action:
                    self.save_undo_state()
                    for b in selected_blocks:
                        if not getattr(b, 'is_locked', False):
                            self.canvas.scene.removeItem(b)
                    self.has_unsaved_changes = True
                    
                event.accept()
                return
                
        QGraphicsView.mousePressEvent(self.canvas, event)

    def duplicate_block(self, block, save_undo=True):
        if save_undo:
            self.save_undo_state()
        
        w_type = block.widget_type
        base_title = block.label.toPlainText()
        
        # Smart auto-incrementing for duplicate names
        if "_Copy" in base_title:
            parts = base_title.rsplit("_", 1)
            if parts[1].isdigit():
                new_title = f"{parts[0]}_{int(parts[1])+1}"
            else:
                new_title = f"{base_title}_2"
        else:
            new_title = f"{base_title}_Copy"
            
        # Offset the clone slightly down and right
        x = block.pos().x() + 20
        y = block.pos().y() + 20
        
        new_block = UIBlock(w_type, new_title, x, y, self.save_undo_state)
        new_block.setRect(0, 0, block.rect().width(), block.rect().height())
        
        # Inherit styling
        new_block.bg_color = block.bg_color
        new_block.text_color = block.text_color
        new_block.alignment = block.alignment
        new_block.valignment = getattr(block, "valignment", "Center")
        new_block.image_path = block.image_path
        new_block.font_family = getattr(block, "font_family", "System Default")
        new_block.font_size = getattr(block, "font_size", 10)
        new_block.border_color = getattr(block, "border_color", "#2c3e50")
        new_block.border_width = getattr(block, "border_width", 2)
        new_block.border_radius = getattr(block, "border_radius", 10 if w_type == "LED" else 5)
        new_block.show_text = getattr(block, "show_text", True)
        new_block.has_shadow = getattr(block, "has_shadow", False)
        new_block.image_scaling = getattr(block, "image_scaling", "Stretch")
        new_block.setZValue(block.zValue() + 0.1)
        new_block.update_visuals()
        
        self.canvas.scene.addItem(new_block)
        self.has_unsaved_changes = True
        
    def select_all(self):
        for item in self.canvas.scene.items():
            if isinstance(item, UIBlock):
                item.setSelected(True)
                
    def nudge_selected(self, dx, dy):
        items = [item for item in self.canvas.scene.selectedItems() if isinstance(item, UIBlock) and not getattr(item, 'is_locked', False)]
        if not items: return
        
        self.save_undo_state()
        for item in items:
            item.setPos(item.pos().x() + dx, item.pos().y() + dy)

    def add_component(self):
        item = self.toolbox.currentItem()
        if not item: return
            
        widget_type = item.text().split(" ")[0]
        title, ok = QInputDialog.getText(self, f"New {widget_type}", f"Enter an identifier name for this {widget_type}:")
        if not ok or not title.strip(): return
            
        # Center of current view
        view_center = self.canvas.mapToScene(self.canvas.viewport().rect().center())
        x = round(view_center.x() / 20.0) * 20.0
        y = round(view_center.y() / 20.0) * 20.0
        
        self.save_undo_state()
        
        block = UIBlock(widget_type, title.strip(), x, y, self.save_undo_state)
        if "G-Code" in widget_type or "3D" in widget_type:
            block.setRect(0, 0, 400, 300) # Make views larger by default
            
        self.canvas.scene.addItem(block)

    def remove_selected(self):
        items_to_remove = [item for item in self.canvas.scene.selectedItems() if not getattr(item, 'is_locked', False)]
        if items_to_remove:
            self.save_undo_state()
            for item in items_to_remove:
                self.canvas.scene.removeItem(item)
            
    def align_selected(self, mode):
        blocks = [item for item in self.canvas.scene.selectedItems() if isinstance(item, UIBlock)]
        if len(blocks) < 2: return
        
        self.save_undo_state()
        
        movable_blocks = [b for b in blocks if not getattr(b, 'is_locked', False)]
        
        if mode == "left":
            target_x = min(b.scenePos().x() for b in blocks)
            for b in movable_blocks: b.setPos(target_x, b.scenePos().y())
        elif mode == "right":
            target_right = max(b.scenePos().x() + b.rect().width() for b in blocks)
            for b in movable_blocks: b.setPos(target_right - b.rect().width(), b.scenePos().y())
        elif mode == "center_x":
            target_center = sum(b.scenePos().x() + b.rect().width()/2 for b in blocks) / len(blocks)
            for b in movable_blocks: b.setPos(target_center - b.rect().width()/2, b.scenePos().y())
        elif mode == "top":
            target_y = min(b.scenePos().y() for b in blocks)
            for b in movable_blocks: b.setPos(b.scenePos().x(), target_y)
        elif mode == "bottom":
            target_bottom = max(b.scenePos().y() + b.rect().height() for b in blocks)
            for b in movable_blocks: b.setPos(b.scenePos().x(), target_bottom - b.rect().height())
        elif mode == "center_y":
            target_center = sum(b.scenePos().y() + b.rect().height()/2 for b in blocks) / len(blocks)
            for b in movable_blocks: b.setPos(b.scenePos().x(), target_center - b.rect().height()/2)
            
    def clear_canvas(self):
        if not self.canvas.scene.items(): return
        reply = QMessageBox.question(self, "Clear Layout", "Are you sure you want to clear the entire canvas to start fresh?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.save_undo_state()
            self.canvas.scene.clear()
            self.has_unsaved_changes = True
            
    def load_layout(self):
        last_dir = self.settings.get_setting("PATHS", "last_ui_dir", os.path.join(os.getcwd(), "ui_layouts"))
        if not os.path.exists(last_dir):
            os.makedirs(last_dir, exist_ok=True)
            
        path, _ = QFileDialog.getOpenFileName(self, "Load UI Layout", last_dir, "Fusion UI (*.fusionui);;All Files (*)")
        if not path: return
        self.settings.save_setting("PATHS", "last_ui_dir", os.path.dirname(path))

        try:
            with open(path, 'r') as f:
                layout_data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load UI layout:\n{e}")
            return
            
        self.save_undo_state()
        self.canvas.scene.clear()
        
        for item in layout_data:
            w_type = item.get("type", "Button")
            title = item.get("title", "Widget")
            x = item.get("x", 0)
            y = item.get("y", 0)
            w = item.get("w", 150)
            h = item.get("h", 60)
            
            block = UIBlock(w_type, title, x, y, self.save_undo_state)
            block.setRect(0, 0, w, h)
            
            block.bg_color = item.get("bg_color", block.bg_color)
            block.text_color = item.get("text_color", block.text_color)
            block.alignment = item.get("alignment", block.alignment)
            block.valignment = item.get("valignment", "Center")
            
            img_path = item.get("image_path", "")
            if img_path and not os.path.isabs(img_path) and not os.path.exists(img_path):
                rel_img_path = os.path.join(os.path.dirname(path), os.path.basename(img_path))
                if os.path.exists(rel_img_path): img_path = rel_img_path
            block.image_path = img_path
            
            block.font_family = item.get("font_family", "System Default")
            block.font_size = item.get("font_size", 10)
            block.border_color = item.get("border_color", "#2c3e50")
            block.border_width = item.get("border_width", 2)
            block.border_radius = item.get("border_radius", 10 if w_type == "LED" else 5)
            block.show_text = item.get("show_text", True)
            block.has_shadow = item.get("has_shadow", False)
            block.image_scaling = item.get("image_scaling", "Stretch")
            block.setZValue(item.get("z_value", 0))
            block.set_locked(item.get("is_locked", False))
            block.update_visuals()
            
            self.canvas.scene.addItem(block)
            
        self.has_unsaved_changes = False

    def get_state(self):
        layout_data = []
        for item in self.canvas.scene.items():
            if isinstance(item, UIBlock):
                layout_data.append({
                    "type": item.widget_type,
                    "title": item.label.toPlainText(),
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "w": item.rect().width(),
                    "h": item.rect().height(),
                    "bg_color": item.bg_color,
                    "text_color": item.text_color,
                    "alignment": item.alignment,
                    "valignment": getattr(item, "valignment", "Center"),
                    "image_path": item.image_path,
                    "font_family": getattr(item, "font_family", "System Default"),
                    "font_size": getattr(item, "font_size", 10),
                    "border_color": getattr(item, "border_color", "#2c3e50"),
                    "border_width": getattr(item, "border_width", 2),
                    "border_radius": getattr(item, "border_radius", 10 if item.widget_type == "LED" else 5),
                    "show_text": getattr(item, "show_text", True),
                    "has_shadow": getattr(item, "has_shadow", False),
                    "image_scaling": getattr(item, "image_scaling", "Stretch"),
                    "z_value": item.zValue(),
                    "is_locked": getattr(item, "is_locked", False)
                })
        # Sort to prevent fake state changes due to list ordering
        return sorted(layout_data, key=lambda k: (k["title"], k["x"], k["y"]))

    def set_state(self, state):
        self._is_restoring = True
        self.canvas.scene.clear()
        for item in state:
            block = UIBlock(item["type"], item["title"], item["x"], item["y"], self.save_undo_state)
            block.setRect(0, 0, item["w"], item["h"])
            
            block.bg_color = item.get("bg_color", block.bg_color)
            block.text_color = item.get("text_color", block.text_color)
            block.alignment = item.get("alignment", block.alignment)
            block.valignment = item.get("valignment", "Center")
            block.image_path = item.get("image_path", "")
            block.font_family = item.get("font_family", "System Default")
            block.font_size = item.get("font_size", 10)
            block.border_color = item.get("border_color", "#2c3e50")
            block.border_width = item.get("border_width", 2)
            block.border_radius = item.get("border_radius", 10 if item.get("type") == "LED" else 5)
            block.show_text = item.get("show_text", True)
            block.has_shadow = item.get("has_shadow", False)
            block.image_scaling = item.get("image_scaling", "Stretch")
            block.setZValue(item.get("z_value", 0))
            block.set_locked(item.get("is_locked", False))
            block.update_visuals()
            
            self.canvas.scene.addItem(block)
        self._is_restoring = False

    def save_undo_state(self):
        if self._is_restoring: return
        curr = self.get_state()
        if self.undo_stack and self.undo_stack[-1] == curr: return
        self.undo_stack.append(curr)
        if len(self.undo_stack) > 50: self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.has_unsaved_changes = True

    def undo(self):
        if not self.undo_stack: return
        self.redo_stack.append(self.get_state())
        self.set_state(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack: return
        self.undo_stack.append(self.get_state())
        self.set_state(self.redo_stack.pop())

    def save_layout(self):
        last_dir = self.settings.get_setting("PATHS", "last_ui_dir", os.path.join(os.getcwd(), "ui_layouts"))
        if not os.path.exists(last_dir):
            os.makedirs(last_dir, exist_ok=True)
            
        path, _ = QFileDialog.getSaveFileName(self, "Save UI Layout", last_dir, "Fusion UI (*.fusionui)")
        if not path: return
        
        if not path.endswith('.fusionui'):
            path += '.fusionui'
            
        self.settings.save_setting("PATHS", "last_ui_dir", os.path.dirname(path))

        layout_data = []
        lib_components = {}

        for item in self.canvas.scene.items():
            if isinstance(item, UIBlock):
                title = item.label.toPlainText()
                w_type = item.widget_type
                
                layout_data.append({
                    "type": w_type,
                    "title": title,
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "w": item.rect().width(),
                    "h": item.rect().height(),
                    "bg_color": item.bg_color,
                    "text_color": item.text_color,
                    "alignment": item.alignment,
                    "valignment": getattr(item, "valignment", "Center"),
                    "image_path": item.image_path,
                    "font_family": getattr(item, "font_family", "System Default"),
                    "font_size": getattr(item, "font_size", 10),
                    "border_color": getattr(item, "border_color", "#2c3e50"),
                    "border_width": getattr(item, "border_width", 2),
                    "border_radius": getattr(item, "border_radius", 10 if item.widget_type == "LED" else 5),
                    "show_text": getattr(item, "show_text", True),
                    "has_shadow": getattr(item, "has_shadow", False),
                    "image_scaling": getattr(item, "image_scaling", "Stretch"),
                    "z_value": item.zValue(),
                    "is_locked": getattr(item, "is_locked", False)
                })

                # --- Dynamic HAL Pin Generator ---
                safe_title = title.replace(' ', '_').lower()
                pins = []
                if w_type == "Button":
                    pins = [["OUT", True, f"ui.{safe_title}.out", "Signal (Blue)"]]
                elif w_type == "Toggle":
                    pins = [["STATE", True, f"ui.{safe_title}.state", "Signal (Blue)"],
                            ["SET_STATE", False, f"ui.{safe_title}.set", "Signal (Blue)"]]
                elif w_type == "DRO":
                    pins = [["VALUE-IN", False, f"ui.{safe_title}.in", "Clock (Yellow)"]]
                elif w_type == "LED":
                    pins = [["STATE-IN", False, f"ui.{safe_title}.in", "Signal (Blue)"]]
                elif w_type == "G-Code":
                    pins = [["FILE-PATH", False, f"ui.{safe_title}.file", "Default"]]
                elif w_type == "3D":
                    pins = [["X-POS", False, f"ui.{safe_title}.x", "Clock (Yellow)"],
                            ["Y-POS", False, f"ui.{safe_title}.y", "Clock (Yellow)"],
                            ["Z-POS", False, f"ui.{safe_title}.z", "Clock (Yellow)"]]

                if pins:
                    comp_name = f"UI_{w_type.replace('-', '')}_{title.replace(' ', '')}"
                    lib_components[comp_name] = pins

        try:
            with open(path, 'w') as f: json.dump(layout_data, f, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save UI layout:\n{e}")
            return
            
        # Clean up recovery file after successful explicit save
        recovery_dir = self.settings.get_setting("PATHS", "recovery_dir", os.path.join(os.getcwd(), "recovery"))
        recovery_path = os.path.join(recovery_dir, "layout_autosave.fusionui")
        if os.path.exists(recovery_path):
            try: os.remove(recovery_path)
            except: pass

        # Inject into Component Library (Graceful Fallback for Standalone Editor Mode)
        if lib_components:
            try:
                if hasattr(self.parent_app, 'library_tab'):
                    lib_data = self.parent_app.library_tab.library_data
                    lib_data.update(lib_components)
                    with open("component_library.json", 'w') as f: json.dump(lib_data, f, indent=4)
                    self.parent_app.library_tab.refresh_list()
                    self.parent_app.wiring_tab.refresh_component_list()
                else:
                    # We are in Standalone Mac Editor mode - Library Tab is not present
                    lib_data = {}
                    if os.path.exists("component_library.json"):
                        with open("component_library.json", 'r') as f: lib_data = json.load(f)
                    lib_data.update(lib_components)
                    with open("component_library.json", 'w') as f: json.dump(lib_data, f, indent=4)
            except Exception as e:
                print(f"Library update skipped (Standalone Mode): {e}")
            
        QMessageBox.information(self, "Saved", f"Layout saved successfully!\n\nGenerated {len(lib_components)} UI components for the Logic Designer.")
        
        self.has_unsaved_changes = False

    def export_for_sharing(self):
        last_dir = self.settings.get_setting("PATHS", "last_export_dir", os.path.join(os.getcwd(), "exports"))
        if not os.path.exists(last_dir):
            os.makedirs(last_dir, exist_ok=True)
            
        export_dir = QFileDialog.getExistingDirectory(self, "Select Directory to Export UI", last_dir)
        if not export_dir: return
        self.settings.save_setting("PATHS", "last_export_dir", export_dir)

        layout_data = []
        lib_components = {}
        readme_lines = ["# Exported UI Details\n", "This directory contains an exported UI layout for CNC Fusion Pro.\n", "## Generated Logic Pins:\n"]

        for item in self.canvas.scene.items():
            if isinstance(item, UIBlock):
                title = item.label.toPlainText()
                w_type = item.widget_type
                
                img_path = item.image_path
                if img_path and os.path.exists(img_path):
                    img_name = os.path.basename(img_path)
                    new_img_path = os.path.join(export_dir, img_name)
                    try:
                        if os.path.abspath(img_path) != os.path.abspath(new_img_path):
                            shutil.copy(img_path, new_img_path)
                        img_path = img_name
                    except Exception as e:
                        print(f"Failed to copy image {img_name}: {e}")
                
                layout_data.append({
                    "type": w_type,
                    "title": title,
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "w": item.rect().width(),
                    "h": item.rect().height(),
                    "bg_color": item.bg_color,
                    "text_color": item.text_color,
                    "alignment": item.alignment,
                    "valignment": getattr(item, "valignment", "Center"),
                    "image_path": img_path,
                    "font_family": getattr(item, "font_family", "System Default"),
                    "font_size": getattr(item, "font_size", 10),
                    "border_color": getattr(item, "border_color", "#2c3e50"),
                    "border_width": getattr(item, "border_width", 2),
                    "border_radius": getattr(item, "border_radius", 10 if item.widget_type == "LED" else 5),
                    "show_text": getattr(item, "show_text", True),
                    "has_shadow": getattr(item, "has_shadow", False),
                    "image_scaling": getattr(item, "image_scaling", "Stretch"),
                    "z_value": item.zValue(),
                    "is_locked": getattr(item, "is_locked", False)
                })

                # --- Dynamic HAL Pin Generator ---
                safe_title = title.replace(' ', '_').lower()
                pins = []
                desc = ""
                if w_type == "Button": 
                    pins = [["OUT", True, f"ui.{safe_title}.out", "Signal (Blue)"]]
                    desc = f"- **{title}** (Button): Outputs a momentary boolean pulse on `ui.{safe_title}.out` when clicked."
                elif w_type == "Toggle": 
                    pins = [["STATE", True, f"ui.{safe_title}.state", "Signal (Blue)"], ["SET_STATE", False, f"ui.{safe_title}.set", "Signal (Blue)"]]
                    desc = f"- **{title}** (Toggle): Outputs its current state on `ui.{safe_title}.state`. Can be toggled programmatically via `ui.{safe_title}.set`."
                elif w_type == "DRO": 
                    pins = [["VALUE-IN", False, f"ui.{safe_title}.in", "Clock (Yellow)"]]
                    desc = f"- **{title}** (DRO): Expects a float value on `ui.{safe_title}.in` to display."
                elif w_type == "LED": 
                    pins = [["STATE-IN", False, f"ui.{safe_title}.in", "Signal (Blue)"]]
                    desc = f"- **{title}** (LED Indicator): Expects a boolean true/false on `ui.{safe_title}.in` to change colors."
                elif w_type == "G-Code": 
                    pins = [["FILE-PATH", False, f"ui.{safe_title}.file", "Default"]]
                    desc = f"- **{title}** (G-Code Editor): String pin on `ui.{safe_title}.file`."
                elif w_type == "3D": 
                    pins = [["X-POS", False, f"ui.{safe_title}.x", "Clock (Yellow)"], ["Y-POS", False, f"ui.{safe_title}.y", "Clock (Yellow)"], ["Z-POS", False, f"ui.{safe_title}.z", "Clock (Yellow)"]]
                    desc = f"- **{title}** (3D Viewer): Expects float positions on `ui.{safe_title}.x`, `.y`, and `.z`."

                if pins: 
                    lib_components[f"UI_{w_type.replace('-', '')}_{title.replace(' ', '')}"] = pins
                    readme_lines.append(desc)

        timestamp = int(time.time())
        try:
            with open(os.path.join(export_dir, f"shared_ui_{timestamp}.fusionui"), 'w') as f: json.dump(layout_data, f, indent=4)
            if lib_components:
                with open(os.path.join(export_dir, f"shared_ui_{timestamp}.mcomp"), 'w') as f: json.dump(lib_components, f, indent=4)
                
                # Generate Readme
                readme_lines.append("\n## Instructions")
                readme_lines.append("To use this UI Layout, drag the `.mcomp` file into the Component Library in CNC Fusion to generate the logic blocks. Then load the `.fusionui` file in your dashboard to view it.")
                
                with open(os.path.join(export_dir, f"README_shared_ui_{timestamp}.md"), 'w') as f: f.write("\n".join(readme_lines))
            
            msg = f"Exported successfully to:\n{export_dir}\n\nFiles generated:\n- shared_ui_{timestamp}.fusionui" + (f"\n- shared_ui_{timestamp}.mcomp\n- README_shared_ui_{timestamp}.md" if lib_components else "")
            QMessageBox.information(self, "Export Successful", msg)
            
            open_reply = QMessageBox.question(self, "Open Folder", "Would you like to open the exported folder now?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if open_reply == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl.fromLocalFile(export_dir))
                
            self.has_unsaved_changes = False
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export UI files:\n{e}")

    def auto_save(self):
        if not self.has_unsaved_changes: return
            
        recovery_dir = self.settings.get_setting("PATHS", "recovery_dir", os.path.join(os.getcwd(), "recovery"))
        if not os.path.exists(recovery_dir):
            os.makedirs(recovery_dir, exist_ok=True)
            
        path = os.path.join(recovery_dir, "layout_autosave.fusionui")
        
        try:
            with open(path, 'w') as f: json.dump(self.get_state(), f, indent=4)
            print(f"[layout_editor_tab.py] Auto-saved recovery file to {path}")
        except Exception as e:
            print(f"[layout_editor_tab.py] Failed to auto-save: {e}")
            
    def check_recovery(self):
        recovery_dir = self.settings.get_setting("PATHS", "recovery_dir", os.path.join(os.getcwd(), "recovery"))
        path = os.path.join(recovery_dir, "layout_autosave.fusionui")
        
        if os.path.exists(path):
            reply = QMessageBox.question(self, "Recover Unsaved Layout", 
                                         "An auto-saved layout was found from a previous session.\nWould you like to recover it?", 
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    with open(path, 'r') as f:
                        state = json.load(f)
                    self.set_state(state)
                    self.has_unsaved_changes = True
                    print("[layout_editor_tab.py] Auto-save recovered successfully.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to recover layout:\n{e}")
            else:
                try: os.remove(path)
                except: pass