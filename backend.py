import os
import subprocess
import shutil
import json

# Auto-detect ffmpeg if in same folder or system path
FFMPEG_BINARY = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BINARY = shutil.which("ffprobe") or "ffprobe"

def get_video_metadata(path):
    """
    Returns detailed metadata: width, height, fps, bitrate, codec, pix_fmt
    """
    if not os.path.exists(path): return None
    
    # We use ffprobe to read file details
    cmd = [
        FFPROBE_BINARY, "-v", "error", 
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,bit_rate,codec_name,pix_fmt", 
        "-of", "json", path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)['streams'][0]
        
        # Calculate FPS from string like "60/1" or "30000/1001"
        if 'r_frame_rate' in data:
            num, den = map(int, data['r_frame_rate'].split('/'))
            fps = num / den if den > 0 else 30.0
        else:
            fps = 30.0
        
        return {
            "width": int(data.get('width', 1920)),
            "height": int(data.get('height', 1080)),
            "fps": fps,
            "pix_fmt": data.get('pix_fmt', 'yuv420p'),
            "codec": data.get('codec_name', 'h264')
        }
    except Exception as e:
        print(f"Metadata Error: {e}")
        return {"width": 1920, "height": 1080, "fps": 30.0, "pix_fmt": "yuv420p"}

def create_short_pro(source_path, clip_data, config, progress_callback=None):
    """
    Processes the video based on user config (Match Source vs Custom).
    """
    if not os.path.exists("output_shorts"):
        os.makedirs("output_shorts")
    
    # 1. Analyze Source
    meta = get_video_metadata(source_path)
    
    # 2. Configure Settings based on User Choice
    mode = config.get("mode", "Custom") # 'Match Source' or 'Custom'
    
    if mode == "Match Source":
        # Keep everything original
        target_h = meta['height']
        target_fps = meta['fps']
        
        # Auto-detect HDR/10-bit logic
        is_10bit = "10le" in meta['pix_fmt'] or "12le" in meta['pix_fmt']
        v_codec = "libx265" if is_10bit else "libx264"
        pix_fmt = meta['pix_fmt'] if is_10bit else "yuv420p"
        
        # Use lower CRF (better quality) for "Match Source" to preserve original look
        crf = "18"
        preset = "fast"
        is_hdr = is_10bit
        
    else:
        # Custom Settings (Force 60FPS, Change Res)
        res_map = {"720p": 1280, "1080p": 1920, "2K": 2560}
        target_h = res_map.get(config.get("resolution", "1080p"), 1920)
        target_fps = 60.0 # Force smooth motion
        
        is_hdr = config.get("hdr", False)
        v_codec = "libx265" if is_hdr else "libx264"
        pix_fmt = "yuv420p10le" if is_hdr else "yuv420p"
        crf = "23" # Standard viral quality
        preset = "fast"

    # 3. Processing Loop
    try:
        temp_dir = "temp_chunks"
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)

        file_list = []
        total_clips = len(clip_data)

        for i, clip in enumerate(clip_data):
            chunk_name = os.path.join(temp_dir, f"chunk_{i}.mp4")
            duration = clip['end'] - clip['start']

            # Filter Chain:
            # 1. crop=ih*(9/16):ih -> Crops the center 9:16 vertical area
            # 2. scale=-2:TARGET_H -> Scales it to desired height (e.g. 1920)
            # 3. fps=TARGET_FPS    -> Converts frame rate
            filters = f"crop=ih*(9/16):ih,scale=-2:{target_h},fps={target_fps}"
            
            cmd = [
                FFMPEG_BINARY, "-y",
                "-ss", str(clip['start']),
                "-t", str(duration),
                "-i", source_path,
                "-vf", filters,
                "-c:v", v_codec,
                "-pix_fmt", pix_fmt,
                "-preset", preset,
                "-crf", crf,
                "-c:a", "aac", "-b:a", "192k",
                chunk_name
            ]
            
            # Add HDR tag if using x265 (HEVC) so phones recognize it
            if "x265" in v_codec:
                cmd.extend(["-tag:v", "hvc1"])

            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            file_list.append(f"file '{os.path.abspath(chunk_name)}'")
            if progress_callback: progress_callback(int(((i+0.5)/total_clips)*100))

        # 4. Merge Chunks
        concat_path = os.path.join(temp_dir, "list.txt")
        with open(concat_path, 'w') as f: f.write("\n".join(file_list))

        # Generate output filename
        res_label = str(meta['height']) if mode == "Match Source" else config.get("resolution")
        out_name = f"short_{mode.replace(' ','')}_{res_label}_{'HDR' if is_hdr else 'SDR'}.mp4"
        output_file = os.path.join("output_shorts", out_name)
        
        subprocess.run([
            FFMPEG_BINARY, "-y", "-f", "concat", "-safe", "0", 
            "-i", concat_path, "-c", "copy", output_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Cleanup
        shutil.rmtree(temp_dir)
        if progress_callback: progress_callback(100)
        return f"Saved: {output_file}"

    except Exception as e:
        return f"Error: {str(e)}"