import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backend


class TestMilestone3AspectRatiosAndMath(unittest.TestCase):
    """Verifies aspect ratio detection, crop math, scaling calculations, and 'Match Source' semantics."""

    def test_landscape_16_9_crop_and_scale(self):
        # 1920x1080 landscape -> 1080p target (1080x1920)
        # 9:16 crop width = round(1080 * 9 / 16) = 608 (snapped even)
        # x_offset = (1920 - 608) // 2 = 656
        res = backend.calculate_crop_and_scale(1920, 1080, "1080p")
        self.assertTrue(res["needs_crop"])
        self.assertEqual(res["crop_w"], 608)
        self.assertEqual(res["crop_h"], 1080)
        self.assertEqual(res["crop_x"], 656)
        self.assertEqual(res["crop_y"], 0)
        self.assertEqual(res["out_w"], 1080)
        self.assertEqual(res["out_h"], 1920)
        self.assertEqual(res["vf"], "crop=608:1080:656:0,scale=1080:1920")

    def test_square_1_1_crop_and_scale(self):
        # 1080x1080 square -> 1080p target (1080x1920)
        # 9:16 crop width = round(1080 * 9 / 16) = 608
        # x_offset = (1080 - 608) // 2 = 236
        res = backend.calculate_crop_and_scale(1080, 1080, "1080p")
        self.assertTrue(res["needs_crop"])
        self.assertEqual(res["crop_w"], 608)
        self.assertEqual(res["crop_h"], 1080)
        self.assertEqual(res["crop_x"], 236)
        self.assertEqual(res["crop_y"], 0)
        self.assertEqual(res["out_w"], 1080)
        self.assertEqual(res["out_h"], 1920)

    def test_native_vertical_9_16_preserves_without_cropping(self):
        # 1080x1920 vertical -> 1080p target
        # Already 9:16 (1080/1920 = 0.5625) -> needs_crop is False, no crop applied
        res = backend.calculate_crop_and_scale(1080, 1920, "1080p")
        self.assertFalse(res["needs_crop"])
        self.assertEqual(res["crop_w"], 1080)
        self.assertEqual(res["crop_h"], 1920)
        self.assertEqual(res["crop_x"], 0)
        self.assertEqual(res["crop_y"], 0)
        self.assertEqual(res["out_w"], 1080)
        self.assertEqual(res["out_h"], 1920)
        self.assertEqual(res["vf"], "scale=1080:1920")

    def test_extra_tall_vertical_9_20_crop_and_scale(self):
        # 1080x2400 (tall phone screen, AR = 0.45 < 9/16) -> 1080p target
        # Crop height = round(1080 * 16 / 9) = 1920
        # y_offset = (2400 - 1920) // 2 = 240
        res = backend.calculate_crop_and_scale(1080, 2400, "1080p")
        self.assertTrue(res["needs_crop"])
        self.assertEqual(res["crop_w"], 1080)
        self.assertEqual(res["crop_h"], 1920)
        self.assertEqual(res["crop_x"], 0)
        self.assertEqual(res["crop_y"], 240)
        self.assertEqual(res["out_w"], 1080)
        self.assertEqual(res["out_h"], 1920)
        self.assertEqual(res["vf"], "crop=1080:1920:0:240,scale=1080:1920")

    def test_dimensions_always_even(self):
        # Test odd/unusual dimensions snap to even values to avoid encoder errors
        odd_inputs = [(1921, 1079), (721, 1281), (853, 480), (1001, 1001)]
        for w, h in odd_inputs:
            for profile in ["720p", "1080p", "2K", "Match Source"]:
                res = backend.calculate_crop_and_scale(w, h, profile)
                self.assertEqual(res["crop_w"] % 2, 0, f"crop_w odd for {w}x{h} on {profile}")
                self.assertEqual(res["crop_h"] % 2, 0, f"crop_h odd for {w}x{h} on {profile}")
                self.assertEqual(res["crop_x"] % 2, 0, f"crop_x odd for {w}x{h} on {profile}")
                self.assertEqual(res["crop_y"] % 2, 0, f"crop_y odd for {w}x{h} on {profile}")
                self.assertEqual(res["out_w"] % 2, 0, f"out_w odd for {w}x{h} on {profile}")
                self.assertEqual(res["out_h"] % 2, 0, f"out_h odd for {w}x{h} on {profile}")

    def test_match_source_vertical_vs_landscape(self):
        # Native vertical source: Match source keeps exact dimensions
        res_vert = backend.calculate_crop_and_scale(1080, 1920, "Match Source")
        self.assertFalse(res_vert["needs_crop"])
        self.assertEqual(res_vert["out_w"], 1080)
        self.assertEqual(res_vert["out_h"], 1920)

        # 4K landscape: Match source maps to 2160x3840 vertical
        res_4k = backend.calculate_crop_and_scale(3840, 2160, "Match Source")
        self.assertEqual(res_4k["out_w"], 2160)
        self.assertEqual(res_4k["out_h"], 3840)

        # 1080p landscape: Match source maps to 1080x1920 vertical
        res_1080p = backend.calculate_crop_and_scale(1920, 1080, "Match Source")
        self.assertEqual(res_1080p["out_w"], 1080)
        self.assertEqual(res_1080p["out_h"], 1920)

        # 720p landscape: Match source maps to 720x1280 vertical
        res_720p = backend.calculate_crop_and_scale(1280, 720, "Match Source")
        self.assertEqual(res_720p["out_w"], 720)
        self.assertEqual(res_720p["out_h"], 1280)


class TestMilestone3MetadataParsing(unittest.TestCase):
    """Verifies audio and color metadata parsing from ffprobe JSON outputs."""

    @patch("backend.os.path.exists")
    @patch("backend.subprocess.run")
    def test_stereo_sdr_metadata_parsed(self, mock_run, mock_exists):
        mock_exists.return_value = True
        probe_json = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "pix_fmt": "yuv420p",
                    "color_primaries": "bt709",
                    "color_transfer": "bt709",
                    "color_space": "bt709"
                },
                {
                    "codec_type": "audio",
                    "channels": 2,
                    "sample_rate": "48000"
                }
            ],
            "format": {"duration": "12.500000"}
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe_json), stderr="")
        meta = backend.get_video_metadata("dummy.mp4")

        self.assertEqual(meta["width"], 1920)
        self.assertEqual(meta["height"], 1080)
        self.assertTrue(meta["has_audio"])
        self.assertEqual(meta["audio_channels"], 2)
        self.assertFalse(meta["is_hdr"])
        self.assertFalse(meta["is_10bit"])
        self.assertEqual(meta["color_primaries"], "bt709")

    @patch("backend.os.path.exists")
    @patch("backend.subprocess.run")
    def test_silent_video_metadata_parsed(self, mock_run, mock_exists):
        mock_exists.return_value = True
        probe_json = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1080,
                    "height": 1920,
                    "r_frame_rate": "60/1",
                    "pix_fmt": "yuv420p",
                }
            ],
            "format": {"duration": "5.000000"}
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe_json), stderr="")
        meta = backend.get_video_metadata("silent.mp4")

        self.assertFalse(meta["has_audio"])
        self.assertEqual(meta["audio_channels"], 0)
        self.assertFalse(meta["is_hdr"])

    @patch("backend.os.path.exists")
    @patch("backend.subprocess.run")
    def test_hdr10_metadata_parsed(self, mock_run, mock_exists):
        mock_exists.return_value = True
        probe_json = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 3840,
                    "height": 2160,
                    "r_frame_rate": "24/1",
                    "pix_fmt": "yuv420p10le",
                    "color_primaries": "bt2020",
                    "color_transfer": "smpte2084",
                    "color_space": "bt2020nc"
                },
                {
                    "codec_type": "audio",
                    "channels": 6
                }
            ],
            "format": {"duration": "60.0"}
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe_json), stderr="")
        meta = backend.get_video_metadata("hdr_movie.mkv")

        self.assertTrue(meta["is_hdr"])
        self.assertTrue(meta["is_10bit"])
        self.assertEqual(meta["color_primaries"], "bt2020")
        self.assertEqual(meta["color_transfer"], "smpte2084")
        self.assertTrue(meta["has_audio"])
        self.assertEqual(meta["audio_channels"], 6)

    @patch("backend.os.path.exists")
    @patch("backend.subprocess.run")
    def test_bt2020_sdr_not_classified_as_hdr(self, mock_run, mock_exists):
        """Wide color primaries (BT.2020) without PQ/HLG transfer must NOT be classified as HDR."""
        mock_exists.return_value = True
        probe_json = {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 3840,
                    "height": 2160,
                    "r_frame_rate": "30/1",
                    "pix_fmt": "yuv420p10le",
                    "color_primaries": "bt2020",
                    "color_transfer": "bt709",
                    "color_space": "bt2020nc"
                }
            ],
            "format": {"duration": "10.0"}
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(probe_json), stderr="")
        meta = backend.get_video_metadata("bt2020_sdr.mp4")

        self.assertFalse(meta["is_hdr"], "BT.2020 primaries alone must not be marked as HDR")
        self.assertTrue(meta["is_10bit"])
        self.assertEqual(meta["color_primaries"], "bt2020")
        self.assertEqual(meta["color_transfer"], "bt709")


class TestMilestone3FFmpegCommandGeneration(unittest.TestCase):
    """Verifies that create_short_pro builds accurate FFmpeg commands for audio, HDR, and trimming."""

    def setUp(self):
        self.tmp_source = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.tmp_source.write(b"dummy")
        self.tmp_source.close()

    def tearDown(self):
        if os.path.exists(self.tmp_source.name):
            os.remove(self.tmp_source.name)

    @patch("backend._generate_unique_output_path", return_value="/tmp/test_out.mp4")
    @patch("backend._run_subprocess")
    @patch("backend.get_video_metadata")
    def test_silent_video_includes_an_flag_and_no_audio_map(self, mock_meta, mock_cmd, mock_gen_path):
        mock_meta.return_value = {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 20.0,
            "has_audio": False,
            "audio_channels": 0,
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
            "pix_fmt": "yuv420p",
            "is_hdr": False,
            "is_10bit": False
        }

        def fake_run(cmd, task_desc=""):
            target_file = cmd[-1]
            if "chunk_" in target_file or target_file.endswith(".mp4"):
                with open(target_file, "wb") as f:
                    f.write(b"fake data")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = fake_run

        backend.create_short_pro(
            self.tmp_source.name,
            [(0.0, 5.0)],
            {"target_res": "1080p", "hdr": False}
        )

        self.assertGreaterEqual(mock_cmd.call_count, 2)
        chunk_call_args = mock_cmd.call_args_list[0][0][0]
        concat_call_args = mock_cmd.call_args_list[1][0][0]

        # Chunk command must have -an and no -map 0:a
        self.assertIn("-an", chunk_call_args)
        self.assertNotIn("-map 0:a:0", " ".join(chunk_call_args))
        self.assertNotIn("-c:a", chunk_call_args)
        # Avoid negative ts must be present
        self.assertIn("-avoid_negative_ts", chunk_call_args)
        self.assertIn("make_zero", chunk_call_args)

        # Concat command must also have -an
        self.assertIn("-an", concat_call_args)

    @patch("backend._generate_unique_output_path", return_value="/tmp/test_out.mp4")
    @patch("backend._run_subprocess")
    @patch("backend.get_video_metadata")
    def test_audio_video_maps_and_downmixes_stereo(self, mock_meta, mock_cmd, mock_gen_path):
        mock_meta.return_value = {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 20.0,
            "has_audio": True,
            "audio_channels": 6,
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
            "pix_fmt": "yuv420p",
            "is_hdr": False,
            "is_10bit": False
        }

        def fake_run(cmd, task_desc=""):
            target_file = cmd[-1]
            if "chunk_" in target_file or target_file.endswith(".mp4"):
                with open(target_file, "wb") as f:
                    f.write(b"fake data")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = fake_run

        backend.create_short_pro(
            self.tmp_source.name,
            [(1.0, 4.0)],
            {"target_res": "1080p", "hdr": False}
        )

        chunk_call_args = mock_cmd.call_args_list[0][0][0]
        cmd_str = " ".join(chunk_call_args)

        self.assertIn("-map 0:v:0", cmd_str)
        self.assertIn("-map 0:a:0", cmd_str)
        self.assertIn("-c:a aac -b:a 192k -ac 2", cmd_str)
        self.assertNotIn("-an", chunk_call_args)

    @patch("backend._generate_unique_output_path", return_value="/tmp/test_out.mp4")
    @patch("backend._run_subprocess")
    @patch("backend.get_video_metadata")
    def test_sdr_source_with_hevc_does_not_fake_hdr_metadata(self, mock_meta, mock_cmd, mock_gen_path):
        mock_meta.return_value = {
            "fps": 30.0,
            "width": 1920,
            "height": 1080,
            "duration": 20.0,
            "has_audio": True,
            "audio_channels": 2,
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
            "pix_fmt": "yuv420p",
            "is_hdr": False,
            "is_10bit": False
        }

        def fake_run(cmd, task_desc=""):
            target_file = cmd[-1]
            if "chunk_" in target_file or target_file.endswith(".mp4"):
                with open(target_file, "wb") as f:
                    f.write(b"fake data")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = fake_run

        # User checked '10-bit / HDR (HEVC)' on SDR source
        backend.create_short_pro(
            self.tmp_source.name,
            [(0.0, 3.0)],
            {"target_res": "1080p", "hdr": True}
        )

        chunk_call_args = mock_cmd.call_args_list[0][0][0]
        cmd_str = " ".join(chunk_call_args)

        # Must use libx265 and 10-bit pixel format
        self.assertIn("-c:v libx265", cmd_str)
        self.assertIn("-pix_fmt yuv420p10le", cmd_str)
        # Must retain BT.709 color tags, NOT fake bt2020 or smpte2084 or hdr10=1
        self.assertIn("-color_primaries bt709", cmd_str)
        self.assertIn("-color_trc bt709", cmd_str)
        self.assertIn("-colorspace bt709", cmd_str)
        self.assertNotIn("hdr10=1", cmd_str)
        self.assertNotIn("smpte2084", cmd_str)

    @patch("backend._generate_unique_output_path", return_value="/tmp/test_out.mp4")
    @patch("backend._run_subprocess")
    @patch("backend.get_video_metadata")
    def test_true_hdr_source_preserves_hdr10_metadata(self, mock_meta, mock_cmd, mock_gen_path):
        mock_meta.return_value = {
            "fps": 24.0,
            "width": 3840,
            "height": 2160,
            "duration": 10.0,
            "has_audio": True,
            "audio_channels": 2,
            "color_primaries": "bt2020",
            "color_transfer": "smpte2084",
            "color_space": "bt2020nc",
            "pix_fmt": "yuv420p10le",
            "is_hdr": True,
            "is_10bit": True
        }

        def fake_run(cmd, task_desc=""):
            target_file = cmd[-1]
            if "chunk_" in target_file or target_file.endswith(".mp4"):
                with open(target_file, "wb") as f:
                    f.write(b"fake data")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_cmd.side_effect = fake_run

        backend.create_short_pro(
            self.tmp_source.name,
            [(0.0, 5.0)],
            {"target_res": "1080p", "hdr": True}
        )

        chunk_call_args = mock_cmd.call_args_list[0][0][0]
        cmd_str = " ".join(chunk_call_args)

        # Must preserve BT.2020 and PQ transfer characteristics
        self.assertIn("-c:v libx265", cmd_str)
        self.assertIn("-color_primaries bt2020", cmd_str)
        self.assertIn("-color_trc smpte2084", cmd_str)
        self.assertIn("-colorspace bt2020nc", cmd_str)
        self.assertIn("hdr10=1", cmd_str)


class TestMilestone3IntegrationRealFFmpeg(unittest.TestCase):
    """End-to-end integration tests using real FFmpeg and FFprobe binary."""

    @classmethod
    def setUpClass(cls):
        # Create small test synthetic videos in a temporary directory
        cls.test_dir = tempfile.mkdtemp(prefix="test_vc_m3_")

        # 1. Landscape 16:9 (640x360) with stereo tone audio
        cls.landscape_video = os.path.join(cls.test_dir, "landscape_stereo.mp4")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            cls.landscape_video
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 2. Native vertical 9:16 (360x640) silent video
        cls.vertical_silent = os.path.join(cls.test_dir, "vertical_silent.mp4")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=360x640:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
            cls.vertical_silent
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        # 3. Square 1:1 (480x480) with 5.1 surround audio
        cls.square_surround = os.path.join(cls.test_dir, "square_surround.mp4")
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=480x480:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "6",
            cls.square_surround
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        # Clean up synthetic test inputs and directory
        for f in os.listdir(cls.test_dir):
            try:
                os.remove(os.path.join(cls.test_dir, f))
            except Exception:
                pass
        try:
            os.rmdir(cls.test_dir)
        except Exception:
            pass

    def test_export_landscape_to_720p_short(self):
        # Export landscape video with stereo audio
        out_path = backend.create_short_pro(
            self.landscape_video,
            [(0.0, 1.0)],
            {"target_res": "720p", "hdr": False}
        )
        self.assertTrue(os.path.exists(out_path))

        meta = backend.get_video_metadata(out_path)
        self.assertEqual(meta["width"], 720)
        self.assertEqual(meta["height"], 1280)
        self.assertTrue(meta["has_audio"])
        self.assertEqual(meta["audio_channels"], 2)
        os.remove(out_path)

    def test_export_vertical_silent_video_succeeds(self):
        # Native vertical silent video must export without crash and keep silent output
        out_path = backend.create_short_pro(
            self.vertical_silent,
            [(0.0, 1.0)],
            {"target_res": "720p", "hdr": False}
        )
        self.assertTrue(os.path.exists(out_path))

        meta = backend.get_video_metadata(out_path)
        self.assertEqual(meta["width"], 720)
        self.assertEqual(meta["height"], 1280)
        self.assertFalse(meta["has_audio"])
        os.remove(out_path)

    def test_export_square_multichannel_downmixes_to_stereo(self):
        # Square 5.1 surround audio video downmixes cleanly to stereo
        out_path = backend.create_short_pro(
            self.square_surround,
            [(0.0, 1.0)],
            {"target_res": "720p", "hdr": False}
        )
        self.assertTrue(os.path.exists(out_path))

        meta = backend.get_video_metadata(out_path)
        self.assertEqual(meta["width"], 720)
        self.assertEqual(meta["height"], 1280)
        self.assertTrue(meta["has_audio"])
        self.assertEqual(meta["audio_channels"], 2)
        os.remove(out_path)


if __name__ == "__main__":
    unittest.main()
