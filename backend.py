import os
import subprocess
import shutil
import json
import tempfile
from datetime import datetime
from pathlib import Path

# Auto-detect ffmpeg if in same folder or system path
FFMPEG_BINARY = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BINARY = shutil.which("ffprobe") or "ffprobe"

def check_external_dependencies():
    """
    Checks that ffmpeg and ffprobe are available and functional.
    Returns (True, "") or raises VideoProcessingError with an actionable message.
    """
    missing = []
    if not shutil.which(FFMPEG_BINARY):
        missing.append("ffmpeg")
    if not shutil.which(FFPROBE_BINARY):
        missing.append("ffprobe")

    if missing:
        raise VideoProcessingError(
            f"Missing required external multimedia dependency: {', '.join(missing)}.\n"
            f"Please install FFmpeg using your system package manager (e.g. 'sudo pacman -S ffmpeg' or 'sudo apt install ffmpeg')."
        )
    return True

class VideoProcessingError(RuntimeError):
    """Raised when metadata probing, video chunking, or concatenation fails."""
    pass

def _run_subprocess(cmd: list, task_desc: str) -> subprocess.CompletedProcess:
    """Executes an external command, captures stdout/stderr, and validates returncode."""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    except FileNotFoundError as exc:
        raise VideoProcessingError(
            f"Required binary '{cmd[0]}' not found. Please verify FFmpeg is installed and on system PATH."
        ) from exc
    except OSError as exc:
        raise VideoProcessingError(f"Failed to execute '{cmd[0]}': {exc}") from exc

    if proc.returncode != 0:
        err_msg = proc.stderr.strip() or proc.stdout.strip() or "No diagnostic output captured."
        if len(err_msg) > 1200:
            err_msg = err_msg[-1200:]
        raise VideoProcessingError(f"{task_desc} failed (exit code {proc.returncode}):\n{err_msg}")
    return proc

def get_video_metadata(path):
    """
    Returns detailed metadata: width, height, fps, bitrate, codec, pix_fmt,
    color_primaries, color_trc, colorspace, is_hdr, is_10bit, has_audio, audio_channels.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Source video not found: {path}")

    # Use ffprobe to inspect both video and audio streams
    cmd = [
        FFPROBE_BINARY, "-v", "error",
        "-show_entries", "stream=index,codec_type,width,height,r_frame_rate,bit_rate,codec_name,pix_fmt,color_primaries,color_transfer,color_space,channels,channel_layout,sample_rate",
        "-of", "json", path
    ]
    try:
        result = _run_subprocess(cmd, f"Probing metadata for '{os.path.basename(path)}'")
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])

        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if v_stream is None:
            raise VideoProcessingError(f"No video streams found in '{path}'.")

        a_streams = [s for s in streams if s.get("codec_type") == "audio"]
        has_audio = len(a_streams) > 0
        audio_channels = int(a_streams[0].get("channels", 2)) if has_audio else 0

        # Calculate FPS from string like "60/1" or "30000/1001"
        if "r_frame_rate" in v_stream:
            try:
                num, den = map(int, v_stream["r_frame_rate"].split("/"))
                fps = num / den if den > 0 else 30.0
            except (ValueError, ZeroDivisionError):
                fps = 30.0
        else:
            fps = 30.0

        pix_fmt = v_stream.get("pix_fmt", "yuv420p")
        color_primaries = v_stream.get("color_primaries")
        color_trc = v_stream.get("color_transfer")
        colorspace = v_stream.get("color_space")

        # True HDR requires HDR transfer characteristics: PQ (smpte2084) or HLG (arib-std-b67).
        # Wide color primaries alone (e.g. BT.2020 SDR) must not be treated as proof of HDR.
        is_hdr = bool(color_trc in ("smpte2084", "arib-std-b67"))
        is_10bit = bool("10le" in pix_fmt or "12le" in pix_fmt)

        return {
            "width": int(v_stream.get("width", 1920)),
            "height": int(v_stream.get("height", 1080)),
            "fps": fps,
            "pix_fmt": pix_fmt,
            "codec": v_stream.get("codec_name", "h264"),
            "color_primaries": color_primaries,
            "color_trc": color_trc,
            "color_transfer": color_trc,
            "colorspace": colorspace,
            "color_space": colorspace,
            "is_hdr": is_hdr,
            "is_10bit": is_10bit,
            "has_audio": has_audio,
            "audio_channels": audio_channels
        }
    except VideoProcessingError:
        raise
    except Exception as e:
        raise VideoProcessingError(f"Failed to parse video metadata: {e}") from e

def calculate_crop_and_scale(src_w: int, src_h: int, mode_or_res: str = "1080p", resolution_choice: str = "1080p") -> dict:
    """
    Calculates exact crop and target scale dimensions for 9:16 vertical video.
    Guarantees even dimensions (required for H.264/H.265) and prevents unnecessary
    cropping of footage that is already 9:16 vertical.

    Accepts (src_w, src_h, resolution_choice) or (src_w, src_h, mode, resolution_choice).
    """
    target_ar = 9.0 / 16.0
    src_ar = src_w / src_h if src_h > 0 else target_ar

    # Normalize mode and resolution_choice arguments
    if mode_or_res in ("720p", "1080p", "2K"):
        mode = "Custom"
        res_choice = mode_or_res
    elif mode_or_res == "Match Source":
        mode = "Match Source"
        res_choice = resolution_choice
    else:
        mode = mode_or_res
        res_choice = resolution_choice

    # Determine target vertical output dimensions
    if mode == "Match Source":
        # Match vertical resolution tier based on source height
        if src_h >= 2160:
            out_w, out_h = 2160, 3840
        elif src_h >= 1440:
            out_w, out_h = 1440, 2560
        elif src_h >= 1080:
            out_w, out_h = 1080, 1920
        elif src_h >= 720:
            out_w, out_h = 720, 1280
        else:
            out_h = src_h - (src_h % 2)
            out_w = int(round(out_h * target_ar))
            out_w -= (out_w % 2)
    else:
        res_map = {
            "720p": (720, 1280),
            "1080p": (1080, 1920),
            "2K": (1440, 2560)
        }
        out_w, out_h = res_map.get(res_choice, (1080, 1920))

    # Check if already 9:16 vertical (within 1% aspect ratio tolerance)
    if abs(src_ar - target_ar) < 0.01:
        if mode == "Match Source":
            out_w = src_w - (src_w % 2)
            out_h = src_h - (src_h % 2)
        scale_filter = f"scale={out_w}:{out_h}"
        return {
            "crop_w": src_w - (src_w % 2),
            "crop_h": src_h - (src_h % 2),
            "crop_x": 0,
            "crop_y": 0,
            "out_w": out_w,
            "out_h": out_h,
            "needs_crop": False,
            "crop_filter": None,
            "scale_filter": scale_filter,
            "vf": scale_filter
        }

    # Crop calculation: wider than 9:16 (landscape, square) -> crop width
    if src_ar > target_ar:
        crop_h = src_h - (src_h % 2)
        crop_w = int(round(crop_h * target_ar))
        crop_w -= (crop_w % 2)
        crop_x = (src_w - crop_w) // 2
        crop_y = 0
    else:
        # Taller than 9:16 (e.g. 9:19.5, 9:20) -> crop height
        crop_w = src_w - (src_w % 2)
        crop_h = int(round(crop_w / target_ar))
        crop_h -= (crop_h % 2)
        crop_x = 0
        crop_y = (src_h - crop_h) // 2

    # Ensure crop coordinates and dimensions stay even and within frame bounds
    crop_x -= (crop_x % 2)
    crop_y -= (crop_y % 2)

    out_w -= (out_w % 2)
    out_h -= (out_h % 2)

    crop_filter = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
    scale_filter = f"scale={out_w}:{out_h}"

    return {
        "crop_w": crop_w,
        "crop_h": crop_h,
        "crop_x": crop_x,
        "crop_y": crop_y,
        "out_w": out_w,
        "out_h": out_h,
        "needs_crop": True,
        "crop_filter": crop_filter,
        "scale_filter": scale_filter,
        "vf": f"{crop_filter},{scale_filter}"
    }

def get_default_output_dir() -> str:
    """
    Returns standard XDG user Videos/Shorts directory if writable,
    falling back to ~/Videos/Shorts or ./output_shorts.
    """
    home = Path.home()
    xdg_videos = os.environ.get("XDG_VIDEOS_DIR")
    if xdg_videos and os.path.isdir(xdg_videos):
        target = Path(xdg_videos) / "Shorts"
    elif (home / "Videos").exists():
        target = home / "Videos" / "Shorts"
    else:
        target = Path.cwd() / "output_shorts"

    try:
        target.mkdir(parents=True, exist_ok=True)
        # Test writability
        test_file = target / ".write_test"
        test_file.touch()
        test_file.unlink()
        return str(target)
    except Exception:
        fallback = Path.cwd() / "output_shorts"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return str(fallback)
        except Exception:
            return tempfile.gettempdir()

def _generate_unique_output_path(source_path: str, mode: str, res_label: str, is_hdr: bool, output_dir: str = None) -> str:
    """Generates a non-colliding, timestamped output path for the exported video."""
    if output_dir is None:
        output_dir = get_default_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    base_stem = Path(source_path).stem if source_path else "video"
    # Sanitize stem for safe filename
    base_stem = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in base_stem)
    mode_tag = mode.replace(" ", "")
    hdr_tag = "HDR" if is_hdr else "SDR"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    base_name = f"{base_stem}_short_{mode_tag}_{res_label}_{hdr_tag}_{timestamp}.mp4"
    candidate = os.path.join(output_dir, base_name)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{base_stem}_short_{mode_tag}_{res_label}_{hdr_tag}_{timestamp}_{counter}.mp4")
        counter += 1
    return candidate

def create_short_pro(source_path, clip_data, config, progress_callback=None):
    """
    Processes the video based on user config (Match Source vs Custom).
    Uses isolated temporary directories, validates subprocess execution, and prevents overwriting.
    """
    if not source_path or not os.path.exists(source_path):
        raise FileNotFoundError(f"Source video not found: {source_path}")
    if not clip_data:
        raise ValueError("Cannot export video: clip queue is empty.")

    output_dir = config.get("output_dir") or get_default_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    # 1. Analyze Source
    meta = get_video_metadata(source_path)

    # 2. Configure Settings based on User Choice & Source Geometry
    mode = config.get("mode", "Custom") # 'Match Source' or 'Custom'
    res_choice = config.get("resolution") or config.get("target_res") or "1080p"
    geometry = calculate_crop_and_scale(meta['width'], meta['height'], mode, res_choice)

    crop_w, crop_h = geometry["crop_w"], geometry["crop_h"]
    crop_x, crop_y = geometry["crop_x"], geometry["crop_y"]
    out_w, out_h = geometry["out_w"], geometry["out_h"]
    needs_crop = geometry["needs_crop"]

    target_fps = meta['fps'] if mode == "Match Source" else 60.0

    # Build video filter chain: crop if needed, scale to exact vertical bounds, force square SAR, set framerate
    filter_parts = []
    if needs_crop:
        filter_parts.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
    filter_parts.append(f"scale={out_w}:{out_h}")
    filter_parts.append("setsar=1")
    filter_parts.append(f"fps={target_fps}")
    filters = ",".join(filter_parts)

    is_hdr_source = meta.get("is_hdr", False)
    is_10bit_source = meta.get("is_10bit", False)

    if mode == "Match Source":
        crf = "18"
        preset = "fast"
        if is_hdr_source:
            v_codec = "libx265"
            pix_fmt = meta.get("pix_fmt", "yuv420p10le")
            is_hdr_export = True
        elif is_10bit_source:
            v_codec = "libx265"
            pix_fmt = meta.get("pix_fmt", "yuv420p10le")
            is_hdr_export = False
        else:
            v_codec = "libx264"
            pix_fmt = "yuv420p"
            is_hdr_export = False
    else:
        # Custom Mode
        crf = "23"
        preset = "fast"
        force_hevc = bool(config.get("hdr", False))
        if is_hdr_source:
            v_codec = "libx265"
            pix_fmt = "yuv420p10le"
            is_hdr_export = True
        elif force_hevc:
            v_codec = "libx265"
            pix_fmt = "yuv420p10le"
            is_hdr_export = False # High-quality 10-bit SDR
        else:
            v_codec = "libx264"
            pix_fmt = "yuv420p"
            is_hdr_export = False

    # 3. Processing in isolated TemporaryDirectory
    with tempfile.TemporaryDirectory(prefix="vc_chunks_") as temp_dir:
        file_list = []
        total_clips = len(clip_data)

        for i, clip in enumerate(clip_data):
            chunk_name = os.path.join(temp_dir, f"chunk_{i}.mp4")
            # Handle both dict format {'start': ..., 'end': ...} and tuple/list format (start, end)
            if isinstance(clip, (tuple, list)):
                start_sec, end_sec = float(clip[0]), float(clip[1])
            else:
                start_sec, end_sec = float(clip['start']), float(clip['end'])
            duration = end_sec - start_sec
            if duration <= 0:
                raise ValueError(f"Clip {i+1} duration ({duration:.2f}s) must be greater than zero.")

            cmd = [
                FFMPEG_BINARY, "-y",
                "-ss", str(start_sec),
                "-t", str(duration),
                "-i", source_path,
                "-avoid_negative_ts", "make_zero",
                "-map", "0:v:0",
                "-vf", filters,
                "-c:v", v_codec,
                "-pix_fmt", pix_fmt,
                "-preset", preset,
                "-crf", crf
            ]

            # Preserve genuine HDR signaling or enforce accurate SDR tags
            if is_hdr_export:
                primaries = meta.get("color_primaries") or "bt2020"
                trc = meta.get("color_trc") or "smpte2084"
                space = meta.get("colorspace") or "bt2020nc"
                cmd.extend([
                    "-color_primaries", primaries,
                    "-color_trc", trc,
                    "-colorspace", space,
                    "-tag:v", "hvc1",
                    "-x265-params", f"hdr10=1:colorprim={primaries}:transfer={trc}:colormatrix={space}"
                ])
            else:
                primaries = meta.get("color_primaries") or "bt709"
                trc = meta.get("color_trc") or "bt709"
                space = meta.get("colorspace") or "bt709"
                cmd.extend([
                    "-color_primaries", primaries,
                    "-color_trc", trc,
                    "-colorspace", space
                ])
                if "x265" in v_codec:
                    cmd.extend(["-tag:v", "hvc1"])

            # Explicit audio handling: map primary audio if available, else omit cleanly
            if meta.get("has_audio", False):
                cmd.extend([
                    "-map", "0:a:0",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ac", "2"
                ])
            else:
                cmd.extend(["-an"])

            cmd.append(chunk_name)

            _run_subprocess(cmd, f"Encoding clip chunk {i+1}/{total_clips}")

            if not os.path.exists(chunk_name) or os.path.getsize(chunk_name) == 0:
                raise VideoProcessingError(f"Chunk {i+1} output file was not created or is empty: {chunk_name}")

            # Escape single quotes for safe FFmpeg concat file directive
            escaped_chunk_path = os.path.abspath(chunk_name).replace("'", "'\\''")
            file_list.append(f"file '{escaped_chunk_path}'")
            if progress_callback:
                progress_callback(int(((i + 0.9) / total_clips) * 90))

        # 4. Merge Chunks
        concat_path = os.path.join(temp_dir, "list.txt")
        with open(concat_path, 'w', encoding="utf-8") as f:
            f.write("\n".join(file_list))

        # Generate non-colliding, unique output filename
        res_label = f"{out_w}x{out_h}" if mode == "Match Source" else str(config.get("resolution", "1080p"))
        output_file = _generate_unique_output_path(source_path, mode, res_label, is_hdr_export, output_dir=output_dir)

        concat_cmd = [
            FFMPEG_BINARY, "-y", "-f", "concat", "-safe", "0",
            "-i", concat_path
        ]
        if meta.get("has_audio", False):
            concat_cmd.extend(["-c", "copy", output_file])
        else:
            concat_cmd.extend(["-c:v", "copy", "-an", output_file])

        _run_subprocess(concat_cmd, "Concatenating clip chunks")

        if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
            raise VideoProcessingError(f"Final exported video was not created or is empty: {output_file}")

        if progress_callback:
            progress_callback(100)
        return output_file
