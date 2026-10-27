import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Provide mock stubs for cv2 and PyQt6 so UI logic can be tested in headless/mock environments
for mod in [
    "cv2", "PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui",
    "PyQt6.sip"
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# Configure basic widget base classes so EditorWindow and ExportThread can be defined
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

import backend
import main


class TestMilestone1SubprocessAndReliability(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="vc_test_env_")
        self.sample_video = os.path.join(self.test_dir, "sample.mp4")
        with open(self.sample_video, "wb") as f:
            f.write(b"\x00" * 1024)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            import shutil
            shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("backend.subprocess.run")
    def test_ffprobe_failure_raises_video_processing_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Invalid data found when processing input file."
        )
        with self.assertRaises(backend.VideoProcessingError) as ctx:
            backend.get_video_metadata(self.sample_video)
        self.assertIn("Invalid data found when processing input file.", str(ctx.exception))
        self.assertIn("exit code 1", str(ctx.exception))

    @patch("backend.get_video_metadata")
    @patch("backend.subprocess.run")
    def test_ffmpeg_chunk_failure_raises_with_diagnostic_stderr(self, mock_run, mock_meta):
        mock_meta.return_value = {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "pix_fmt": "yuv420p",
            "codec": "h264"
        }
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Unknown encoder 'libx265'. Cannot initialize codec."
        )
        clips = [{"start": 0.0, "end": 5.0}]
        config = {"mode": "Custom", "resolution": "1080p", "hdr": True}

        with self.assertRaises(backend.VideoProcessingError) as ctx:
            backend.create_short_pro(self.sample_video, clips, config)
        self.assertIn("Unknown encoder 'libx265'", str(ctx.exception))
        self.assertIn("Encoding clip chunk 1/1 failed", str(ctx.exception))

    @patch("backend.get_video_metadata")
    @patch("backend.subprocess.run")
    def test_isolated_tempdir_cleanup_on_success(self, mock_run, mock_meta):
        mock_meta.return_value = {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "pix_fmt": "yuv420p",
            "codec": "h264"
        }

        created_temp_dirs = []

        def side_effect(cmd, **kwargs):
            # Check if command is chunk encoding or concat
            target_file = cmd[-1]
            if "chunk_" in target_file or target_file.endswith(".mp4"):
                parent_dir = os.path.dirname(target_file)
                if "vc_chunks_" in parent_dir and parent_dir not in created_temp_dirs:
                    created_temp_dirs.append(parent_dir)
                with open(target_file, "wb") as f:
                    f.write(b"dummy video data")
            return MagicMock(returncode=0, stdout="{}", stderr="")

        mock_run.side_effect = side_effect
        clips = [{"start": 0.0, "end": 2.0}, {"start": 3.0, "end": 5.0}]
        config = {"mode": "Match Source"}

        output = backend.create_short_pro(self.sample_video, clips, config)
        self.assertTrue(os.path.exists(output))
        self.assertGreater(len(created_temp_dirs), 0)

        # Verify temporary directory was cleaned up and no longer exists
        for temp_dir in created_temp_dirs:
            self.assertFalse(os.path.exists(temp_dir), f"Temp dir was not cleaned up: {temp_dir}")

        # Clean up output file
        if os.path.exists(output):
            os.remove(output)

    @patch("backend.get_video_metadata")
    @patch("backend.subprocess.run")
    def test_isolated_tempdir_cleanup_on_exception(self, mock_run, mock_meta):
        mock_meta.return_value = {
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "pix_fmt": "yuv420p",
            "codec": "h264"
        }

        observed_temp_dirs = []

        def failing_side_effect(cmd, **kwargs):
            target_file = cmd[-1]
            parent_dir = os.path.dirname(target_file)
            if "vc_chunks_" in parent_dir:
                observed_temp_dirs.append(parent_dir)
            return MagicMock(returncode=1, stdout="", stderr="Disk full while writing chunk")

        mock_run.side_effect = failing_side_effect
        clips = [{"start": 1.0, "end": 4.0}]
        config = {"mode": "Custom"}

        with self.assertRaises(backend.VideoProcessingError):
            backend.create_short_pro(self.sample_video, clips, config)

        self.assertGreater(len(observed_temp_dirs), 0)
        for temp_dir in observed_temp_dirs:
            self.assertFalse(os.path.exists(temp_dir), f"Temp dir leaked after error: {temp_dir}")

    def test_output_filename_collision_prevention(self):
        output_dir = os.path.join(self.test_dir, "outputs")
        os.makedirs(output_dir, exist_ok=True)

        path1 = backend._generate_unique_output_path(
            self.sample_video, "Match Source", "1080", is_hdr=False, output_dir=output_dir
        )
        with open(path1, "w") as f:
            f.write("existing file 1")

        path2 = backend._generate_unique_output_path(
            self.sample_video, "Match Source", "1080", is_hdr=False, output_dir=output_dir
        )
        self.assertNotEqual(path1, path2)
        self.assertFalse(os.path.exists(path2))

    def test_input_validation_empty_clips_and_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            backend.create_short_pro("nonexistent_video.mp4", [{"start": 0, "end": 1}], {})

        with self.assertRaises(ValueError):
            backend.create_short_pro(self.sample_video, [], {})


class TestMilestone1UIBehavior(unittest.TestCase):
    def test_on_done_preserves_clip_queue_on_failure(self):
        # Test the UI contract without initializing a full QApplication/display server
        mock_window = MagicMock()
        mock_window.clip_queue = [{"start": 1.0, "end": 4.0}]
        mock_window.list_clips = MagicMock()
        mock_window.btn_exp = MagicMock()
        mock_window.pbar_win = MagicMock()

        # Import EditorWindow.on_done implementation logic directly
        import main
        with patch("main.QMessageBox.critical") as mock_crit, \
             patch("main.QMessageBox.information") as mock_info:

            # Simulate failure
            main.EditorWindow.on_done(mock_window, False, "Simulated transcode crash")

            # Queue must NOT be cleared on failure
            self.assertEqual(len(mock_window.clip_queue), 1)
            mock_window.list_clips.clear.assert_not_called()
            mock_crit.assert_called_once()
            mock_info.assert_not_called()
            mock_window.btn_exp.setEnabled.assert_called_with(True)

    def test_on_done_clears_clip_queue_on_success(self):
        mock_window = MagicMock()
        mock_window.clip_queue = [{"start": 1.0, "end": 4.0}]
        mock_window.list_clips = MagicMock()
        mock_window.btn_exp = MagicMock()
        mock_window.pbar_win = MagicMock()

        import main
        with patch("main.QMessageBox.critical") as mock_crit, \
             patch("main.QMessageBox.information") as mock_info:

            # Simulate success
            main.EditorWindow.on_done(mock_window, True, "Saved: output_shorts/sample.mp4")

            # Queue should be cleared on success
            self.assertEqual(len(mock_window.clip_queue), 0)
            mock_window.list_clips.clear.assert_called_once()
            mock_info.assert_called_once()
            mock_crit.assert_not_called()
            mock_window.btn_exp.setEnabled.assert_called_with(True)

    def test_export_video_guards_against_concurrent_execution(self):
        mock_window = MagicMock()
        mock_window.clip_queue = [{"start": 0.0, "end": 2.0}]
        mock_window.thread = MagicMock()
        mock_window.thread.isRunning.return_value = True

        with patch("main.QMessageBox.warning") as mock_warn:
            main.EditorWindow.export_video(mock_window)
            mock_warn.assert_called_once()
            self.assertIn("Export in Progress", mock_warn.call_args[0][1])

    @patch("backend.create_short_pro")
    def test_export_thread_emits_success_signal(self, mock_create):
        mock_create.return_value = "output_shorts/result.mp4"
        thread = main.ExportThread("input.mp4", [{"start": 0, "end": 5}], {})
        thread.finished_signal = MagicMock()

        thread.run()
        thread.finished_signal.emit.assert_called_once_with(True, "Saved: output_shorts/result.mp4")

    @patch("backend.create_short_pro")
    def test_export_thread_emits_failure_signal_on_exception(self, mock_create):
        mock_create.side_effect = backend.VideoProcessingError("Transcode failed")
        thread = main.ExportThread("input.mp4", [{"start": 0, "end": 5}], {})
        thread.finished_signal = MagicMock()

        thread.run()
        thread.finished_signal.emit.assert_called_once_with(False, "Transcode failed")


if __name__ == "__main__":
    unittest.main()
