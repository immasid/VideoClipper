import sys
import cv2
import backend
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QListWidget, 
                             QFileDialog, QMessageBox, QFrame, QSizePolicy, 
                             QDialog, QProgressBar, QComboBox, QCheckBox)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QRectF
from PyQt6.QtGui import QPainter, QColor, QImage, QPixmap, QFont, QPen, QLinearGradient

# --- GEMINI THEME COLORS ---
# We define them here to use in the custom painting logic
THEME_BG = QColor(14, 14, 14)       # Deepest background
THEME_SURFACE = QColor(30, 31, 32)  # Card background
THEME_ACCENT_1 = QColor(76, 141, 246) # Google Blue
THEME_ACCENT_2 = QColor(124, 77, 255) # Deep Purple
THEME_TEXT = QColor(227, 227, 227)
THEME_TEXT_SEC = QColor(148, 148, 148)

# --- WORKER THREAD (Unchanged) ---
class ExportThread(QThread):
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(str)

    def __init__(self, video_path, clip_queue, config):
        super().__init__()
        self.video_path = video_path
        self.clip_queue = clip_queue
        self.config = config

    def run(self):
        msg = backend.create_short_pro(
            self.video_path, 
            self.clip_queue, 
            self.config,
            progress_callback=self.emit_progress
        )
        self.finished_signal.emit(msg)

    def emit_progress(self, val):
        self.progress_signal.emit(val)

# --- ZOOMABLE TRIMMER (Styled) ---
class ZoomableTrimmer(QWidget):
    rangeChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120) # Slightly taller for better spacing
        
        self.duration_sec = 100.0
        self.start_sec = 0.0
        self.end_sec = 10.0
        self.zoom_level = 1.0
        self.scroll_offset = 0.0
        self.dragging_start = False
        self.dragging_end = False
        self.panning = False
        self.last_mouse_x = 0
        self.handle_width = 16 # Sleeker handles
        self.min_px_gap = 15

    def set_duration(self, seconds):
        self.duration_sec = max(0.1, seconds)
        self.start_sec = 0.0
        self.end_sec = min(10.0, self.duration_sec)
        self.zoom_level = 1.0
        self.scroll_offset = 0.0
        self.update()

    def time_to_pixel(self, seconds):
        virtual_width = self.width() * self.zoom_level
        pct = seconds / self.duration_sec
        return (pct * virtual_width) - self.scroll_offset

    def pixel_to_time(self, px):
        virtual_width = self.width() * self.zoom_level
        virtual_px = px + self.scroll_offset
        pct = virtual_px / virtual_width
        return max(0.0, min(self.duration_sec, pct * self.duration_sec))

    def wheelEvent(self, event):
        zoom_factor = 1.1 if event.angleDelta().y() > 0 else 0.9
        mouse_x = event.position().x()
        time_at_mouse = self.pixel_to_time(mouse_x)
        self.zoom_level = max(1.0, min(50.0, self.zoom_level * zoom_factor))
        virtual_width = self.width() * self.zoom_level
        new_pixel_for_time = (time_at_mouse / self.duration_sec) * virtual_width
        self.scroll_offset = max(0, min(virtual_width - self.width(), new_pixel_for_time - mouse_x))
        self.update()

    def mousePressEvent(self, event):
        x = event.pos().x()
        self.last_mouse_x = x
        s_px = self.time_to_pixel(self.start_sec)
        e_px = self.time_to_pixel(self.end_sec)
        
        dist_start = abs(x - s_px)
        dist_end = abs(x - e_px)
        hit_radius = 40
        
        if dist_start < hit_radius and dist_start < dist_end:
            self.dragging_start = True
        elif dist_end < hit_radius:
            self.dragging_end = True
        else:
            self.panning = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        x = event.pos().x()
        delta = x - self.last_mouse_x
        self.last_mouse_x = x
        
        pixel_scale = (self.width() * self.zoom_level) / self.duration_sec
        min_time_gap = self.min_px_gap / pixel_scale
        
        if self.dragging_start:
            new_t = self.pixel_to_time(x)
            self.start_sec = min(new_t, self.end_sec - min_time_gap)
            self.rangeChanged.emit(self.start_sec, self.end_sec)
            self.update()
        elif self.dragging_end:
            new_t = self.pixel_to_time(x)
            self.end_sec = max(new_t, self.start_sec + min_time_gap)
            self.rangeChanged.emit(self.start_sec, self.end_sec)
            self.update()
        elif self.panning:
            max_scroll = (self.width() * self.zoom_level) - self.width()
            self.scroll_offset = max(0.0, min(max_scroll, self.scroll_offset - delta))
            self.update()

    def mouseReleaseEvent(self, event):
        self.dragging_start = False
        self.dragging_end = False
        self.panning = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        # 1. Background (Dark)
        painter.fillRect(0, 0, w, h, THEME_SURFACE)
        
        # 2. Time Ticks
        if self.zoom_level < 2: step = 10
        elif self.zoom_level < 5: step = 5
        elif self.zoom_level < 15: step = 1
        else: step = 0.5
        
        start_vis = self.pixel_to_time(0)
        end_vis = self.pixel_to_time(w)
        curr_t = (int(start_vis // step) * step)
        
        painter.setPen(QPen(QColor(60, 60, 60), 1))
        painter.setFont(QFont("Segoe UI", 8))
        
        while curr_t < end_vis:
            px = int(self.time_to_pixel(curr_t))
            painter.drawLine(px, 0, px, h)
            # Text color slightly lighter
            painter.setPen(THEME_TEXT_SEC)
            painter.drawText(px + 4, 15, f"{curr_t:.1f}s")
            painter.setPen(QPen(QColor(60, 60, 60), 1))
            curr_t += step

        # 3. Track & Selection
        track_y = 30
        track_h = h - 30
        x_start = int(self.time_to_pixel(self.start_sec))
        x_end = int(self.time_to_pixel(self.end_sec))
        
        # Unselected areas (Darker overlay)
        painter.fillRect(0, track_y, x_start, track_h, QColor(0, 0, 0, 200))
        painter.fillRect(x_end, track_y, w - x_end, track_h, QColor(0, 0, 0, 200))
        
        # Selected Area (Gemini Gradient feel - translucent blue/purple)
        selection_color = QColor(76, 141, 246, 50) # Transparent Blue
        painter.fillRect(x_start, track_y, x_end - x_start, track_h, selection_color)
        
        # Top/Bottom Borders of selection
        pen_border = QPen(THEME_ACCENT_1)
        pen_border.setWidth(2)
        painter.setPen(pen_border)
        painter.drawLine(x_start, track_y, x_end, track_y)
        painter.drawLine(x_start, track_y + track_h, x_end, track_y + track_h)

        handle_w = self.handle_width
        
        def draw_handle(x_pos, is_left):
            h_top = track_y + 5
            h_height = track_h - 10
            
            painter.setPen(Qt.PenStyle.NoPen)
            
            # Gradient Brush for Handle (Gemini Sparkle)
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, THEME_ACCENT_1)
            grad.setColorAt(1.0, THEME_ACCENT_2)
            painter.setBrush(grad)
            
            # Draw Pill Shape Handle
            rect = QRectF(x_pos - handle_w if is_left else x_pos, h_top, handle_w, h_height)
            painter.drawRoundedRect(rect, 8.0, 8.0)
            
            # Draw Vertical Line
            painter.setPen(QPen(THEME_ACCENT_1, 2))
            painter.drawLine(x_pos, track_y, x_pos, track_y + track_h)

            # Little grip lines on handle
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
            center_x = x_pos - (handle_w / 2) if is_left else x_pos + (handle_w / 2)
            painter.drawLine(int(center_x), int(h_top) + 10, int(center_x), int(h_top) + h_height - 10)

        if x_start > -50 and x_start < w + 50: draw_handle(x_start, is_left=True)
        if x_end > -50 and x_end < w + 50: draw_handle(x_end, is_left=False)

# --- MAIN WINDOW ---
class EditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ProShorts Studio • Gemini Edition")
        self.setGeometry(100, 100, 1200, 850)
        
        # --- GLOBAL STYLESHEET ---
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0e0e0e;
            }
            QWidget {
                color: #E3E3E3;
                font-family: 'Segoe UI', sans-serif;
                font-size: 14px;
            }
            /* Frames & Containers */
            QFrame {
                background-color: #1e1f20;
                border: 1px solid #333;
                border-radius: 12px;
            }
            /* Specific frame for video preview to be black */
            QLabel#VideoScreen {
                background-color: #000000;
                border: 1px solid #333;
                border-radius: 12px;
            }
            /* Buttons */
            QPushButton {
                background-color: #2d2e30;
                border: 1px solid #444;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3d3e40;
                border-color: #555;
            }
            QPushButton:pressed {
                background-color: #222;
            }
            /* Gradient 'Export' Button */
            QPushButton#BtnExport {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4c8df6, stop:1 #7c4dff);
                border: none;
                color: white;
                font-weight: bold;
            }
            QPushButton#BtnExport:hover {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5c9eff, stop:1 #8c5eff);
            }
            /* Accent 'Add' Button */
            QPushButton#BtnAdd {
                background-color: #3c4043;
                border: 1px solid #4c8df6;
                color: #8ab4f8;
            }
            QPushButton#BtnAdd:hover {
                background-color: #494c50;
            }
            /* Inputs */
            QComboBox {
                background-color: #2d2e30;
                border: 1px solid #444;
                border-radius: 6px;
                padding: 4px 10px;
                selection-background-color: #4c8df6;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #555;
                background: #2d2e30;
            }
            QCheckBox::indicator:checked {
                background-color: #4c8df6;
                border-color: #4c8df6;
            }
            /* Lists */
            QListWidget {
                background-color: #1e1f20;
                border: 1px solid #333;
                border-radius: 12px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
            }
            QListWidget::item:selected {
                background-color: #3c4043;
                color: #8ab4f8;
            }
            /* Scrollbars */
            QScrollBar:vertical {
                border: none;
                background: #1e1f20;
                width: 8px;
                margin: 0px; 
            }
            QScrollBar::handle:vertical {
                background: #555;
                min-height: 20px;
                border-radius: 4px;
            }
        """)
        
        self.video_path = None
        self.cap = None
        self.clip_queue = []
        self.timer = QTimer()
        self.timer.timeout.connect(self.next_frame)

        central = QWidget()
        self.setCentralWidget(central)
        
        # Main Layout with margins
        layout = QVBoxLayout(central)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- TOP BAR ---
        top_frame = QFrame()
        top_frame.setFixedHeight(60)
        top_layout = QHBoxLayout(top_frame)
        top_layout.setContentsMargins(15, 0, 15, 0)
        
        lbl_profile = QLabel("Export Profile:")
        lbl_profile.setStyleSheet("color: #9aa0a6; font-weight: bold;")
        top_layout.addWidget(lbl_profile)
        
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Match Source (Recommended)", "Custom Options"])
        self.combo_mode.currentIndexChanged.connect(self.toggle_settings)
        top_layout.addWidget(self.combo_mode)
        
        # Vertical Separator
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet("border: none; background-color: #444; width: 1px;")
        line.setFixedWidth(1)
        top_layout.addWidget(line)
        
        self.lbl_res = QLabel("Resolution:")
        top_layout.addWidget(self.lbl_res)
        
        self.combo_res = QComboBox()
        self.combo_res.addItems(["1080p", "2K", "720p"])
        top_layout.addWidget(self.combo_res)
        
        self.chk_hdr = QCheckBox("Force HDR (HEVC)")
        top_layout.addWidget(self.chk_hdr)
        
        top_layout.addStretch()
        layout.addWidget(top_frame)
        self.toggle_settings(0)

        # --- PREVIEW AREA ---
        self.screen = QLabel("Load Video")
        self.screen.setObjectName("VideoScreen") # For styling
        self.screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Placeholder styling
        self.screen.setStyleSheet("QLabel#VideoScreen { color: #555; font-size: 16px; background-color: #000; }")
        layout.addWidget(self.screen)

        # --- CONTROLS AREA ---
        controls = QFrame()
        c_layout = QVBoxLayout(controls)
        c_layout.setSpacing(10)
        
        # Info Label
        self.lbl_info = QLabel("Scroll timeline to Zoom • Drag background to Pan")
        self.lbl_info.setStyleSheet("color: #9aa0a6; font-size: 12px; border: none; background: transparent;")
        self.lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.lbl_info)
        
        # Trimmer
        self.trimmer = ZoomableTrimmer()
        self.trimmer.rangeChanged.connect(self.on_trim_update)
        c_layout.addWidget(self.trimmer)
        
        # Play Button (Centered)
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(50, 50)
        self.btn_play.setStyleSheet("""
            QPushButton {
                background-color: #4c8df6; 
                border-radius: 25px; 
                font-size: 20px; 
                color: white;
            }
            QPushButton:hover { background-color: #5c9eff; }
        """)
        self.btn_play.clicked.connect(self.toggle_play)
        
        h_center = QHBoxLayout()
        h_center.addWidget(self.btn_play)
        c_layout.addLayout(h_center)
        
        layout.addWidget(controls)

        # --- BOTTOM ACTION BAR ---
        b_layout = QHBoxLayout()
        b_layout.setSpacing(15)
        
        btn_load = QPushButton("📂 Load Video")
        btn_load.clicked.connect(self.load_video)
        b_layout.addWidget(btn_load)
        
        btn_add = QPushButton("➕ Add to Queue")
        btn_add.setObjectName("BtnAdd")
        btn_add.clicked.connect(self.add_clip)
        b_layout.addWidget(btn_add)
        
        b_layout.addStretch()
        
        self.btn_exp = QPushButton("✨ Export Shorts")
        self.btn_exp.setObjectName("BtnExport") # Triggers gradient style
        self.btn_exp.setMinimumWidth(150)
        self.btn_exp.clicked.connect(self.export_video)
        b_layout.addWidget(self.btn_exp)
        
        layout.addLayout(b_layout)

        # --- QUEUE LIST ---
        self.list_clips = QListWidget()
        self.list_clips.setMaximumHeight(80)
        layout.addWidget(self.list_clips)

    def toggle_settings(self, index):
        is_custom = (index == 1)
        self.combo_res.setEnabled(is_custom)
        self.chk_hdr.setEnabled(is_custom)
        self.lbl_res.setEnabled(is_custom)
        
        disabled_style = "color: #555; border: 1px solid #333;"
        enabled_style = "" # Reverts to global stylesheet
        
        if not is_custom:
            self.combo_res.setStyleSheet(disabled_style)
            self.chk_hdr.setStyleSheet("color: #555;")
            self.lbl_res.setStyleSheet("color: #555; border: none; background: transparent;")
        else:
            self.combo_res.setStyleSheet(enabled_style)
            self.chk_hdr.setStyleSheet("")
            self.lbl_res.setStyleSheet("color: #e3e3e3; border: none; background: transparent;")

    def load_video(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open", "", "Video (*.mp4 *.mkv)")
        if f:
            self.video_path = f
            self.cap = cv2.VideoCapture(f)
            total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
            self.trimmer.set_duration(total/fps)
            self.update_preview(0)
            self.screen.setStyleSheet("QLabel#VideoScreen { background-color: #000; border: 1px solid #333; }")

    def toggle_play(self):
        if not self.video_path: return
        if self.timer.isActive(): 
            self.timer.stop(); self.btn_play.setText("▶")
        else: 
            fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
            self.timer.start(int(1000/fps)); self.btn_play.setText("⏸")

    def next_frame(self):
        if not self.cap: return
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        end_f = int(self.trimmer.end_sec * fps)
        curr = self.cap.get(cv2.CAP_PROP_POS_FRAMES)
        if curr >= end_f:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.trimmer.start_sec * fps)
        ret, frame = self.cap.read()
        if ret: self.display_frame(frame)

    def display_frame(self, frame):
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch*w, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(img).scaled(self.screen.size(), Qt.AspectRatioMode.KeepAspectRatio)
        self.screen.setPixmap(pix)

    def update_preview(self, frame_no):
        if not self.cap: return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ret, f = self.cap.read(); 
        if ret: self.display_frame(f)

    def on_trim_update(self, s, e):
        if self.cap is None: return 
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        target = int(s * fps) if self.trimmer.dragging_start else int(e * fps)
        self.update_preview(target)
        self.lbl_info.setText(f"Selection: {s:.2f}s  →  {e:.2f}s  (Duration: {e-s:.2f}s)")

    def add_clip(self):
        if not self.video_path: return
        self.clip_queue.append({'start': self.trimmer.start_sec, 'end': self.trimmer.end_sec})
        self.list_clips.addItem(f"🎬 Clip {len(self.clip_queue)}: {self.trimmer.start_sec:.2f}s - {self.trimmer.end_sec:.2f}s")

    def export_video(self):
        if not self.clip_queue: 
            QMessageBox.warning(self, "No Clips", "Please add at least one clip to the queue.")
            return
        mode = self.combo_mode.currentText()
        config = {
            "mode": "Match Source" if "Match" in mode else "Custom",
            "resolution": self.combo_res.currentText(),
            "hdr": self.chk_hdr.isChecked()
        }
        self.pbar_win = QDialog(self)
        self.pbar_win.setWindowTitle("Processing...")
        self.pbar_win.setFixedSize(300, 100)
        self.pbar_win.setStyleSheet("background-color: #1e1f20; color: #e3e3e3;")
        l = QVBoxLayout(self.pbar_win)
        l.addWidget(QLabel("Rendering Video..."))
        self.p_bar = QProgressBar()
        self.p_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #444; border-radius: 5px; text-align: center; }
            QProgressBar::chunk { background-color: #4c8df6; width: 20px; }
        """)
        l.addWidget(self.p_bar)
        self.pbar_win.show()
        
        self.thread = ExportThread(self.video_path, self.clip_queue, config)
        self.thread.progress_signal.connect(self.p_bar.setValue)
        self.thread.finished_signal.connect(self.on_done)
        self.thread.start()

    def on_done(self, msg):
        self.pbar_win.close()
        QMessageBox.information(self, "Success", msg)
        self.clip_queue = []
        self.list_clips.clear()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = EditorWindow()
    window.show()
    sys.exit(app.exec())