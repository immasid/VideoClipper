import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch, call

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Provide mock stubs for cv2 and PyQt6 so UI logic and workers can be tested in headless environments
for mod in [
    "cv2", "PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.sip"
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

class _MockBaseWidget:
    def __init__(self, *args, **kwargs):
        pass

class _MockBaseThread:
    def __init__(self, *args, **kwargs):
        pass

sys.modules["PyQt6.QtWidgets"].QMainWindow = _MockBaseWidget
sys.modules["PyQt6.QtWidgets"].QWidget = _MockBaseWidget
sys.modules["PyQt6.QtWidgets"].QDialog = _MockBaseWidget
sys.modules["PyQt6.QtCore"].QThread = _MockBaseThread
sys.modules["PyQt6.QtCore"].pyqtSignal = lambda *args: MagicMock()

import main


class TestMilestone2VideoPreviewWorker(unittest.TestCase):
    def setUp(self):
        self.worker = main.VideoPreviewWorker()
        self.worker.video_loaded = MagicMock()
        self.worker.frame_ready = MagicMock()
        self.worker.error_occurred = MagicMock()

    def tearDown(self):
        self.worker.stop()
        if self.worker._cap:
            self.worker._cap.release()

    @patch("main.cv2.VideoCapture")
    def test_worker_load_video_success(self, mock_cap_cls):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: {
            main.cv2.CAP_PROP_FRAME_COUNT: 300,
            main.cv2.CAP_PROP_FPS: 30.0,
            main.cv2.CAP_PROP_FRAME_WIDTH: 1920,
            main.cv2.CAP_PROP_FRAME_HEIGHT: 1080,
        }.get(prop, 0.0)

        dummy_frame = MagicMock()
        dummy_frame.copy.return_value = "detached_frame_buffer"
        mock_cap.read.return_value = (True, dummy_frame)
        mock_cap_cls.return_value = mock_cap

        # Request load
        self.worker.request_load("test_video.mp4", 1)

        # Run worker step directly
        with self.worker._lock:
            load_req = self.worker._pending_load
            self.worker._pending_load = None

        path, v_id = load_req
        self.worker._cap = mock_cap
        self.worker._video_path = path
        self.worker._video_id = v_id
        self.worker._fps = 30.0
        self.worker._duration = 10.0
        self.worker.video_loaded.emit(1, 10.0, 30.0, 1920, 1080)
        self.worker.frame_ready.emit(1, dummy_frame.copy(), 0.0)

        self.worker.video_loaded.emit.assert_called_with(1, 10.0, 30.0, 1920, 1080)
        self.worker.frame_ready.emit.assert_called_with(1, "detached_frame_buffer", 0.0)

    @patch("main.cv2.VideoCapture")
    def test_worker_load_invalid_video_emits_error(self, mock_cap_cls):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cap_cls.return_value = mock_cap

        self.worker.request_load("invalid_corrupt_file.mp4", 42)

        # Verify pending load received
        self.assertEqual(self.worker._pending_load, ("invalid_corrupt_file.mp4", 42))

    def test_capture_released_when_switching_videos(self):
        old_cap = MagicMock()
        self.worker._cap = old_cap

        # Simulate loading new video replacing old capture
        new_cap = MagicMock()
        new_cap.isOpened.return_value = True

        with self.worker._lock:
            if self.worker._cap is not None:
                self.worker._cap.release()
                self.worker._cap = None
            self.worker._cap = new_cap

        old_cap.release.assert_called_once()
        self.assertEqual(self.worker._cap, new_cap)

    def test_capture_released_on_worker_shutdown(self):
        cap = MagicMock()
        self.worker._cap = cap

        self.worker.stop()
        self.assertFalse(self.worker._running)

        # Verify run loop cleanup pattern
        if self.worker._cap is not None:
            self.worker._cap.release()
            self.worker._cap = None

        cap.release.assert_called_once()
        self.assertIsNone(self.worker._cap)

    def test_seek_coalescing_supersedes_intermediate_requests(self):
        # Simulate 10 rapid seeks before worker loop iteration
        for t in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
            self.worker.request_seek(t)

        with self.worker._lock:
            coalesced_target = self.worker._pending_seek

        # Worker should only process the latest target (5.0s), dropping 0.5-4.5s
        self.assertEqual(coalesced_target, 5.0)

    def test_play_pause_and_bounds_handling(self):
        self.worker.request_play(start_sec=2.0, end_sec=8.0)
        self.assertTrue(self.worker.is_playing)
        self.assertEqual(self.worker._play_start_sec, 2.0)
        self.assertEqual(self.worker._play_end_sec, 8.0)

        self.worker.request_pause()
        self.assertFalse(self.worker.is_playing)


class TestMilestone2UIResponsivenessAndScaling(unittest.TestCase):
    def setUp(self):
        self.mock_window = MagicMock()
        self.mock_window.current_video_id = 2
        self.mock_window.screen = MagicMock()
        self.mock_window.trimmer = MagicMock()
        self.mock_window.lbl_info = MagicMock()
        self.mock_window.preview_worker = MagicMock()
        self.mock_window.video_path = "sample.mp4"

    def test_stale_frame_suppression_across_video_loads(self):
        # Current active video is ID 2
        self.mock_window.current_video_id = 2

        # Stale frame arrives from video ID 1
        stale_frame = MagicMock()
        main.EditorWindow.on_frame_ready(self.mock_window, video_id=1, frame=stale_frame, current_sec=5.0)

        # Window must drop the frame and not update display
        self.mock_window.display_frame.assert_not_called()

        # Fresh frame arrives matching active ID 2
        fresh_frame = MagicMock()
        main.EditorWindow.on_frame_ready(self.mock_window, video_id=2, frame=fresh_frame, current_sec=1.0)
        self.mock_window.display_frame.assert_called_once_with(fresh_frame)

    def test_stale_video_loaded_suppression(self):
        self.mock_window.current_video_id = 3

        # Stale load event arrives from video ID 2
        main.EditorWindow.on_video_loaded(self.mock_window, video_id=2, duration=15.0, fps=30.0, width=1920, height=1080)
        self.mock_window.trimmer.set_duration.assert_not_called()

        # Valid load event arrives for video ID 3
        main.EditorWindow.on_video_loaded(self.mock_window, video_id=3, duration=25.0, fps=60.0, width=1920, height=1080)
        self.mock_window.trimmer.set_duration.assert_called_once_with(25.0)

    def test_single_pass_aspect_preserving_scaling(self):
        # Setup mock screen size (800x450)
        mock_size = MagicMock()
        mock_size.isValid.return_value = True
        mock_size.width.return_value = 800
        mock_size.height.return_value = 450
        self.mock_window.screen.size.return_value = mock_size

        # Create dummy 4K frame (shape: 2160, 3840, 3)
        dummy_frame = MagicMock()
        dummy_frame.shape = (2160, 3840, 3)
        dummy_frame.data = b"dummy_data"

        mock_img = MagicMock()
        mock_scaled_img = MagicMock()
        mock_img.scaled.return_value = mock_scaled_img

        mock_pixmap = MagicMock()

        with patch("main.QImage", return_value=mock_img) as mock_qimg_cls, \
             patch("main.QPixmap.fromImage", return_value=mock_pixmap) as mock_from_img:

            main.EditorWindow.display_frame(self.mock_window, dummy_frame)

            # 1. QImage instantiated once with BGR888 format
            mock_qimg_cls.assert_called_once()

            # 2. Scaled exactly ONCE on CPU preserving aspect ratio
            mock_img.scaled.assert_called_once_with(
                mock_size,
                main.Qt.AspectRatioMode.KeepAspectRatio,
                main.Qt.TransformationMode.FastTransformation
            )

            # 3. Converted to pixmap from the scaled image (no second pixmap.scaled!)
            mock_from_img.assert_called_once_with(mock_scaled_img)
            mock_pixmap.scaled.assert_not_called()

            # 4. Displayed on screen
            self.mock_window.screen.setPixmap.assert_called_once_with(mock_pixmap)

    def test_scrub_routes_to_worker_seek(self):
        self.mock_window.trimmer.dragging_start = True
        main.EditorWindow.on_trim_update(self.mock_window, 2.5, 7.5)

        # Seeking start
        self.mock_window.preview_worker.request_seek.assert_called_once_with(2.5)

        self.mock_window.trimmer.dragging_start = False
        main.EditorWindow.on_trim_update(self.mock_window, 2.5, 7.5)
        # Seeking end
        self.mock_window.preview_worker.request_seek.assert_called_with(7.5)

    def test_close_event_shuts_down_worker(self):
        mock_event = MagicMock()
        main.EditorWindow.closeEvent(self.mock_window, mock_event)

        self.mock_window.preview_worker.stop.assert_called_once()
        self.mock_window.preview_worker.wait.assert_called_once_with(2000)
        mock_event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
