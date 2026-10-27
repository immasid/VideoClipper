import sys
import os
import time
import threading
import cv2
import backend
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QListWidget,
                             QFileDialog, QMessageBox, QFrame, QSizePolicy,
                             QDialog, QProgressBar, QComboBox, QCheckBox)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QRectF
from PyQt6.QtGui import QPainter, QColor, QImage, QPixmap, QFont, QPen, QLinearGradient

# --- GEMINI THEME COLORS ---
THEME_BG = QColor(14, 14, 14)
THEME_SURFACE = QColor(30, 31, 32)
THEME_ACCENT_1 = QColor(76, 141, 246)
THEME_ACCENT_2 = QColor(124, 77, 255)
THEME_TEXT = QColor(227, 227, 227)
THEME_TEXT_SEC = QColor(148, 148, 148)

# --- WORKER THREAD ---
class ExportThread(QThread):
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, video_path, clip_queue, config):
        super().__init__()
        self.video_path = video_path
        self.clip_queue = list(clip_queue)
        self.config = config

    def run(self):
        try:
            output_file = backend.create_short_pro(
                self.video_path,
                self.clip_queue,
                self.config,
                progress_callback=self.emit_progress
            )
            self.finished_signal.emit(True, f"Saved: {output_file}")
        except Exception as e:
            self.finished_signal.emit(False, str(e))

    def emit_progress(self, val):
        self.progress_signal.emit(val)

# --- VIDEO PREVIEW WORKER ---
class VideoPreviewWorker(QThread):
    video_loaded = pyqtSignal(int, float, float, int, int) # video_id, duration, fps, width, height
    frame_ready = pyqtSignal(int, object, float)           # video_id, frame_bgr_array, current_sec
    error_occurred = pyqtSignal(int, str)                  # video_id, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._running = True
        self._cap = None
        self._video_path = None
        self._video_id = -1
        self._fps = 30.0
        self._duration = 0.0
        self._is_playing = False
        self._play_start_sec = 0.0
        self._play_end_sec = 0.0
        self._pending_load = None # (path, video_id)
        self._pending_seek = None # float target_sec

    @property
    def is_playing(self):
        with self._lock:
            return self._is_playing

    def request_load(self, path, video_id):
        with self._lock:
            self._pending_load = (path, video_id)
            self._is_playing = False
            self._pending_seek = None
            self._wake_event.set()

    def request_seek(self, target_sec):
        with self._lock:
            self._pending_seek = max(0.0, float(target_sec))
            self._wake_event.set()

    def request_play(self, start_sec, end_sec):
        with self._lock:
            self._play_start_sec = max(0.0, float(start_sec))
            self._play_end_sec = max(self._play_start_sec + 0.05, float(end_sec))
            self._is_playing = True
            self._wake_event.set()

    def request_pause(self):
        with self._lock:
            self._is_playing = False
            self._wake_event.set()

    def stop(self):
        with self._lock:
            self._running = False
            self._is_playing = False
            self._wake_event.set()

    def run(self):
        while True:
            with self._lock:
                if not self._running:
                    break
                load_req = self._pending_load
                self._pending_load = None
                seek_req = self._pending_seek
                self._pending_seek = None
                playing = self._is_playing
                start_sec = self._play_start_sec
                end_sec = self._play_end_sec
                video_id = self._video_id
                fps = self._fps

            # 1. Handle Video Load Request
            if load_req is not None:
                path, v_id = load_req
                if self._cap is not None:
                    self._cap.release()
                    self._cap = None
                try:
                    cap = cv2.VideoCapture(path)
                    if not cap.isOpened():
                        self.error_occurred.emit(v_id, f"Failed to open video: {path}")
                        continue
                    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                    fps_val = cap.get(cv2.CAP_PROP_FPS) or 30.0
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
                    duration = (total / fps_val) if fps_val > 0 else 0.0

                    self._cap = cap
                    self._video_path = path
                    self._video_id = v_id
                    self._fps = fps_val
                    self._duration = duration

                    self.video_loaded.emit(v_id, duration, fps_val, w, h)

                    # Read and emit initial frame
                    ret, first_frame = self._cap.read()
                    if ret and first_frame is not None:
                        self.frame_ready.emit(v_id, first_frame.copy(), 0.0)
                except Exception as exc:
                    self.error_occurred.emit(v_id, f"Error initializing video capture: {exc}")
                continue

            # 2. Handle Coalesced Seek Request (Picks latest, superseding obsolete requests)
            if seek_req is not None and self._cap is not None and self._cap.isOpened():
                try:
                    target_frame = int(seek_req * fps)
                    self._cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                    ret, frame = self._cap.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(video_id, frame.copy(), seek_req)
                except Exception:
                    pass
                continue

            # 3. Handle Playback Frame Advance
            if playing and self._cap is not None and self._cap.isOpened():
                t_decode_start = time.perf_counter()
                try:
                    curr_frame = self._cap.get(cv2.CAP_PROP_POS_FRAMES)
                    curr_sec = curr_frame / fps if fps > 0 else 0.0
                    if curr_sec >= end_sec:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_sec * fps))
                        curr_sec = start_sec

                    ret, frame = self._cap.read()
                    if ret and frame is not None:
                        self.frame_ready.emit(video_id, frame.copy(), curr_sec)
                    else:
                        # Loop back to start if read failed or EOF
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_sec * fps))
                except Exception:
                    pass

                decode_duration = time.perf_counter() - t_decode_start
                frame_interval = 1.0 / fps if fps > 0 else 0.033
                sleep_time = max(0.001, frame_interval - decode_duration)
                self._wake_event.wait(timeout=sleep_time)
                self._wake_event.clear()
                continue

            # 4. Idle Wait
            self._wake_event.wait(timeout=0.05)
            self._wake_event.clear()

        # Clean up capture on worker thread termination
        if self._cap is not None:
            self._cap.release()
            self._cap = None

# --- ZOOMABLE TRIMMER (Optimized) ---
class ZoomableTrimmer(QWidget):
    rangeChanged = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)

        self.duration_sec = 100.0
        self.start_sec = 0.0
        self.end_sec = 10.0
        self.zoom_level = 1.0
        self.scroll_offset = 0.0
        self.dragging_start = False
        self.dragging_end = False
        self.panning = False
        self.last_mouse_x = 0
        self.handle_width = 16
        self.min_px_gap = 15

        # OPTIMIZATION: Pre-calculate reusable drawing objects to save CPU cycles in paint loop
        self.brush_bg = THEME_SURFACE
        self.pen_tick = QPen(QColor(60, 60, 60), 1)
        self.pen_text = THEME_TEXT_SEC
        self.brush_overlay = QColor(0, 0, 0, 200)
        self.brush_selection = QColor(76, 141, 246, 50)
        self.pen_border = QPen(THEME_ACCENT_1)
        self.pen_border.setWidth(2)
        self.pen_handle_line = QPen(THEME_ACCENT_1, 2)
        self.pen_grip = QPen(QColor(255, 255, 255, 200), 1)

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
        # FastAntialiasing is slightly faster than standard Antialiasing
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 1. Background
        painter.fillRect(0, 0, w, h, self.brush_bg)

        # 2. Time Ticks
        if self.zoom_level < 2: step = 10
        elif self.zoom_level < 5: step = 5
        elif self.zoom_level < 15: step = 1
        else: step = 0.5

        start_vis = self.pixel_to_time(0)
        end_vis = self.pixel_to_time(w)
        curr_t = (int(start_vis // step) * step)

        painter.setFont(QFont("Segoe UI", 8))

        while curr_t < end_vis:
            px = int(self.time_to_pixel(curr_t))
            painter.setPen(self.pen_tick)
            painter.drawLine(px, 0, px, h)

            painter.setPen(self.pen_text)
            painter.drawText(px + 4, 15, f"{curr_t:.1f}s")
            curr_t += step

        # 3. Track & Selection
        track_y = 30
        track_h = h - 30
        x_start = int(self.time_to_pixel(self.start_sec))
        x_end = int(self.time_to_pixel(self.end_sec))

        # Unselected areas
        painter.fillRect(0, track_y, x_start, track_h, self.brush_overlay)
        painter.fillRect(x_end, track_y, w - x_end, track_h, self.brush_overlay)

        # Selected Area
        painter.fillRect(x_start, track_y, x_end - x_start, track_h, self.brush_selection)

        # Borders
        painter.setPen(self.pen_border)
        painter.drawLine(x_start, track_y, x_end, track_y)
        painter.drawLine(x_start, track_y + track_h, x_end, track_y + track_h)

        handle_w = self.handle_width

        def draw_handle(x_pos, is_left):
            h_top = track_y + 5
            h_height = track_h - 10

            painter.setPen(Qt.PenStyle.NoPen)

            # Gradient Brush
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0.0, THEME_ACCENT_1)
            grad.setColorAt(1.0, THEME_ACCENT_2)
            painter.setBrush(grad)

            rect = QRectF(x_pos - handle_w if is_left else x_pos, h_top, handle_w, h_height)
            painter.drawRoundedRect(rect, 8.0, 8.0)

            painter.setPen(self.pen_handle_line)
            painter.drawLine(x_pos, track_y, x_pos, track_y + track_h)

            painter.setPen(self.pen_grip)
            center_x = x_pos - (handle_w / 2) if is_left else x_pos + (handle_w / 2)
            painter.drawLine(int(center_x), int(h_top) + 10, int(center_x), int(h_top) + h_height - 10)

        if x_start > -50 and x_start < w + 50: draw_handle(x_start, is_left=True)
        if x_end > -50 and x_end < w + 50: draw_handle(x_end, is_left=False)

def find_app_icon():
    """Finds installed or local icon file for VideoClipper."""
    candidates = [
        # Standard system icon locations
        "/usr/share/icons/hicolor/scalable/apps/videoclipper.svg",
        "/usr/local/share/icons/hicolor/scalable/apps/videoclipper.svg",
        os.path.expanduser("~/.local/share/icons/hicolor/scalable/apps/videoclipper.svg"),
        # Relative to installed script location
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "share", "icons", "hicolor", "scalable", "apps", "videoclipper.svg")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "assets", "videoclipper.svg")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "videoclipper.svg")),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None

# --- MAIN WINDOW ---
class EditorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VideoClipper")
        self.setGeometry(100, 100, 1200, 850)

        self.setStyleSheet("""
            QMainWindow { background-color: #0e0e0e; }
            QWidget { color: #E3E3E3; font-family: 'Segoe UI', sans-serif; font-size: 14px; }
            QFrame { background-color: #1e1f20; border: 1px solid #333; border-radius: 12px; }
            QLabel#VideoScreen { background-color: #000000; border: 1px solid #333; border-radius: 12px; }
            QPushButton { background-color: #2d2e30; border: 1px solid #444; border-radius: 8px; padding: 8px 16px; font-weight: 500; }
            QPushButton:hover { background-color: #3d3e40; border-color: #555; }
            QPushButton:pressed { background-color: #222; }
            QPushButton#BtnExport { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4c8df6, stop:1 #7c4dff); border: none; color: white; font-weight: bold; }
            QPushButton#BtnExport:hover { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5c9eff, stop:1 #8c5eff); }
            QPushButton#BtnAdd { background-color: #3c4043; border: 1px solid #4c8df6; color: #8ab4f8; }
            QPushButton#BtnAdd:hover { background-color: #494c50; }
            QComboBox { background-color: #2d2e30; border: 1px solid #444; border-radius: 6px; padding: 4px 10px; selection-background-color: #4c8df6; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #555; background: #2d2e30; }
            QCheckBox::indicator:checked { background-color: #4c8df6; border-color: #4c8df6; }
            QListWidget { background-color: #1e1f20; border: 1px solid #333; border-radius: 12px; padding: 5px; }
            QListWidget::item { padding: 8px; border-radius: 6px; }
            QListWidget::item:selected { background-color: #3c4043; color: #8ab4f8; }
            QScrollBar:vertical { border: none; background: #1e1f20; width: 8px; margin: 0px; }
            QScrollBar::handle:vertical { background: #555; min-height: 20px; border-radius: 4px; }
        """)

        self.video_path = None
        self.current_video_id = 0
        self.clip_queue = []

        # Background preview decoding worker
        self.preview_worker = VideoPreviewWorker()
        self.preview_worker.video_loaded.connect(self.on_video_loaded)
        self.preview_worker.frame_ready.connect(self.on_frame_ready)
        self.preview_worker.error_occurred.connect(self.on_preview_error)
        self.preview_worker.start()

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Top Bar
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

        self.chk_hdr = QCheckBox("10-bit / HDR (HEVC)")
        self.chk_hdr.setToolTip("Export using 10-bit HEVC (H.265). Automatically preserves true HDR metadata if the source is HDR; preserves SDR color for SDR footage.")
        top_layout.addWidget(self.chk_hdr)

        top_layout.addStretch()
        layout.addWidget(top_frame)
        self.toggle_settings(0)

        # Preview Area
        self.screen = QLabel("Load Video")
        self.screen.setObjectName("VideoScreen")
        self.screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.screen.setStyleSheet("QLabel#VideoScreen { color: #555; font-size: 16px; background-color: #000; }")
        layout.addWidget(self.screen)

        # Controls Area
        controls = QFrame()
        c_layout = QVBoxLayout(controls)
        c_layout.setSpacing(10)

        self.lbl_info = QLabel("Scroll timeline to Zoom • Drag background to Pan")
        self.lbl_info.setStyleSheet("color: #9aa0a6; font-size: 12px; border: none; background: transparent;")
        self.lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.lbl_info)

        self.trimmer = ZoomableTrimmer()
        self.trimmer.rangeChanged.connect(self.on_trim_update)
        c_layout.addWidget(self.trimmer)

        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(50, 50)
        self.btn_play.setStyleSheet("QPushButton { background-color: #4c8df6; border-radius: 25px; font-size: 20px; color: white; } QPushButton:hover { background-color: #5c9eff; }")
        self.btn_play.clicked.connect(self.toggle_play)

        h_center = QHBoxLayout()
        h_center.addWidget(self.btn_play)
        c_layout.addLayout(h_center)

        layout.addWidget(controls)

        # Bottom Bar
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
        self.btn_exp.setObjectName("BtnExport")
        self.btn_exp.setMinimumWidth(150)
        self.btn_exp.clicked.connect(self.export_video)
        b_layout.addWidget(self.btn_exp)

        layout.addLayout(b_layout)

        self.list_clips = QListWidget()
        self.list_clips.setMaximumHeight(80)
        layout.addWidget(self.list_clips)

    def toggle_settings(self, index):
        is_custom = (index == 1)
        self.combo_res.setEnabled(is_custom)
        self.chk_hdr.setEnabled(is_custom)
        self.lbl_res.setEnabled(is_custom)

        if not is_custom:
            self.combo_res.setStyleSheet("color: #555; border: 1px solid #333;")
            self.chk_hdr.setStyleSheet("color: #555;")
            self.lbl_res.setStyleSheet("color: #555; border: none; background: transparent;")
        else:
            self.combo_res.setStyleSheet("")
            self.chk_hdr.setStyleSheet("")
            self.lbl_res.setStyleSheet("color: #e3e3e3; border: none; background: transparent;")

    def load_video(self):
        f, _ = QFileDialog.getOpenFileName(self, "Open", "", "Video (*.mp4 *.mkv)")
        if f:
            self.video_path = f
            self.current_video_id += 1
            self.btn_play.setText("▶")
            self.preview_worker.request_load(f, self.current_video_id)

    def on_video_loaded(self, video_id, duration, fps, width, height):
        if video_id != self.current_video_id:
            return
        self.trimmer.set_duration(duration)
        self.screen.setStyleSheet("QLabel#VideoScreen { background-color: #000; border: 1px solid #333; }")

    def on_preview_error(self, video_id, error_msg):
        if video_id != self.current_video_id:
            return
        QMessageBox.warning(self, "Preview Error", error_msg)

    def toggle_play(self):
        if not self.video_path: return
        if self.preview_worker.is_playing:
            self.preview_worker.request_pause()
            self.btn_play.setText("▶")
        else:
            self.preview_worker.request_play(self.trimmer.start_sec, self.trimmer.end_sec)
            self.btn_play.setText("⏸")

    def on_frame_ready(self, video_id, frame, current_sec):
        if video_id != self.current_video_id:
            return
        self.display_frame(frame)

    def display_frame(self, frame):
        # Single-pass aspect-preserving scaling on GUI thread
        h, w, ch = frame.shape
        bytes_per_line = ch * w

        # 1. Direct memory access using BGR format
        img = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)

        # 2. Scale once to target_size preserving aspect ratio without redundant double scaling
        target_size = self.screen.size()
        if target_size.isValid() and (target_size.width() < w or target_size.height() < h):
            scaled_img = img.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation
            )
            pix = QPixmap.fromImage(scaled_img)
        else:
            pix = QPixmap.fromImage(img)

        self.screen.setPixmap(pix)

    def update_preview(self, frame_no):
        if not self.video_path: return
        fps = getattr(self.preview_worker, '_fps', 30.0) or 30.0
        self.preview_worker.request_seek(frame_no / fps)

    def on_trim_update(self, s, e):
        if not self.video_path: return
        target_sec = s if self.trimmer.dragging_start else e
        self.preview_worker.request_seek(target_sec)
        self.lbl_info.setText(f"Selection: {s:.2f}s  →  {e:.2f}s  (Duration: {e-s:.2f}s)")

    def add_clip(self):
        if not self.video_path: return
        self.clip_queue.append({'start': self.trimmer.start_sec, 'end': self.trimmer.end_sec})
        self.list_clips.addItem(f"🎬 Clip {len(self.clip_queue)}: {self.trimmer.start_sec:.2f}s - {self.trimmer.end_sec:.2f}s")

    def export_video(self):
        if not self.clip_queue:
            QMessageBox.warning(self, "No Clips", "Please add at least one clip to the queue.")
            return
        if hasattr(self, 'thread') and self.thread and self.thread.isRunning():
            QMessageBox.warning(self, "Export in Progress", "An export is already running. Please wait for it to complete.")
            return
        mode = self.combo_mode.currentText()
        config = {
            "mode": "Match Source" if "Match" in mode else "Custom",
            "resolution": self.combo_res.currentText(),
            "hdr": self.chk_hdr.isChecked()
        }
        self.btn_exp.setEnabled(False)
        self.pbar_win = QDialog(self)
        self.pbar_win.setWindowTitle("Processing...")
        self.pbar_win.setFixedSize(300, 100)
        self.pbar_win.setModal(True)
        self.pbar_win.setStyleSheet("background-color: #1e1f20; color: #e3e3e3;")
        l = QVBoxLayout(self.pbar_win)
        l.addWidget(QLabel("Rendering Video..."))
        self.p_bar = QProgressBar()
        self.p_bar.setStyleSheet("QProgressBar { border: 1px solid #444; border-radius: 5px; text-align: center; } QProgressBar::chunk { background-color: #4c8df6; width: 20px; }")
        l.addWidget(self.p_bar)
        self.pbar_win.show()

        self.thread = ExportThread(self.video_path, self.clip_queue, config)
        self.thread.progress_signal.connect(self.p_bar.setValue)
        self.thread.finished_signal.connect(self.on_done)
        self.thread.start()

    def on_done(self, success, msg):
        if hasattr(self, 'pbar_win') and self.pbar_win:
            self.pbar_win.close()
        self.btn_exp.setEnabled(True)
        if success:
            QMessageBox.information(self, "Success", msg)
            self.clip_queue = []
            self.list_clips.clear()
        else:
            QMessageBox.critical(self, "Export Failed", f"An error occurred during export:\n\n{msg}")

    def closeEvent(self, event):
        if hasattr(self, 'preview_worker') and self.preview_worker:
            self.preview_worker.stop()
            self.preview_worker.wait(2000)
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VideoClipper")
    app.setApplicationDisplayName("VideoClipper")
    app.setDesktopFileName("videoclipper")

    # Check for required FFmpeg external dependency
    try:
        backend.check_external_dependencies()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Missing Dependencies",
            f"VideoClipper cannot start because required multimedia tools are missing:\n\n{exc}"
        )
        sys.exit(1)

    # Set application icon if found
    icon_path = find_app_icon()
    if icon_path:
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon_path))

    window = EditorWindow()
    # If a video file was passed via command line (e.g. file manager 'Open With'), load it
    args = app.arguments()
    if len(args) > 1 and os.path.isfile(args[1]):
        initial_file = os.path.abspath(args[1])
        window.video_path = initial_file
        window.current_video_id += 1
        window.btn_play.setText("▶")
        window.preview_worker.request_load(initial_file, window.current_video_id)

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()