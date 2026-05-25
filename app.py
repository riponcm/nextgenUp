#!/usr/bin/env python3
"""Upscale4K — AI-powered video upscaling server."""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

def _find_bin(name):
    """Find a binary in PATH or common locations."""
    found = shutil.which(name)
    if found:
        return found
    for d in ['/usr/local/bin', '/opt/homebrew/bin', '/opt/anaconda3/bin', '/usr/bin']:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return name

FFMPEG = _find_bin('ffmpeg')
FFPROBE = _find_bin('ffprobe')

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['OUTPUT_FOLDER'] = 'outputs'
app.config['FRAMES_FOLDER'] = 'frames'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

tasks = {}


@app.route('/')
def index():
    model_exists = os.path.exists('static/models/realesr-general-x4v3.onnx')
    return render_template('index.html', model_available=model_exists)


@app.route('/api/upload', methods=['POST'])
def upload():
    file = request.files.get('video')
    if not file or not file.filename:
        return jsonify({'error': 'No video file provided'}), 400

    allowed = {'.mp4', '.mov', '.webm', '.mkv', '.avi'}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return jsonify({'error': f'Unsupported format: {ext}'}), 400

    task_id = uuid.uuid4().hex[:12]
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
    filename = f"{task_id}_{safe_name}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    info = get_video_info(filepath)
    if not info:
        os.remove(filepath)
        return jsonify({'error': 'Could not read video file'}), 400

    tasks[task_id] = {
        'status': 'uploaded',
        'input': filepath,
        'filename': file.filename,
        'info': info,
        'progress': 0,
        'current_frame': 0,
        'total_frames': int(info['duration'] * info['fps']),
        'message': 'Ready',
    }

    return jsonify({'task_id': task_id, 'info': info})


@app.route('/api/upscale/basic', methods=['POST'])
def upscale_basic():
    data = request.json or {}
    task_id = data.get('task_id')
    scale = int(data.get('scale', 4))

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    task['status'] = 'processing'
    task['progress'] = 0
    task['message'] = 'Starting FFmpeg...'

    thread = threading.Thread(target=_run_basic_upscale, args=(task_id, scale), daemon=True)
    thread.start()

    return jsonify({'status': 'processing'})


@app.route('/api/progress/<task_id>')
def progress(task_id):
    def generate():
        while True:
            task = tasks.get(task_id)
            if not task:
                yield f"data: {json.dumps({'status': 'error', 'message': 'Task not found'})}\n\n"
                break
            yield f"data: {json.dumps({k: task[k] for k in ('status', 'progress', 'message', 'current_frame', 'total_frames')})}\n\n"
            if task['status'] in ('completed', 'error'):
                break
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/download/<task_id>')
def download(task_id):
    task = tasks.get(task_id)
    if not task or task.get('status') != 'completed':
        return jsonify({'error': 'Not ready'}), 404

    output_path = task.get('output')
    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'Output file missing'}), 404

    stem = Path(task['filename']).stem
    download_name = f"{stem}_4K.mp4"
    return send_file(output_path, as_attachment=True, download_name=download_name)


# --- Pro mode: receive upscaled frames from browser WebGPU ---

@app.route('/api/pro/start', methods=['POST'])
def pro_start():
    data = request.json or {}
    task_id = data.get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    frames_dir = os.path.join(app.config['FRAMES_FOLDER'], task_id)
    os.makedirs(frames_dir, exist_ok=True)

    task['status'] = 'pro_processing'
    task['progress'] = 0
    task['message'] = 'Receiving upscaled frames...'
    task['frames_dir'] = frames_dir
    task['frames_received'] = 0

    return jsonify({'status': 'ready'})


@app.route('/api/pro/frame/<task_id>/<int:frame_num>', methods=['POST'])
def pro_frame(task_id, frame_num):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    frames_dir = task.get('frames_dir')
    if not frames_dir:
        return jsonify({'error': 'Pro processing not started'}), 400

    frame_data = request.data
    if not frame_data:
        return jsonify({'error': 'No frame data'}), 400

    frame_path = os.path.join(frames_dir, f"frame_{frame_num:05d}.jpg")
    with open(frame_path, 'wb') as f:
        f.write(frame_data)

    task['frames_received'] = frame_num + 1
    total = task['total_frames']
    task['progress'] = min(95, int((frame_num + 1) / total * 95))
    task['current_frame'] = frame_num + 1
    task['message'] = f'Frame {frame_num + 1}/{total}'

    return jsonify({'status': 'ok'})


@app.route('/api/pro/assemble', methods=['POST'])
def pro_assemble():
    data = request.json or {}
    task_id = data.get('task_id')
    fps = data.get('fps', 24)

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    task['message'] = 'Assembling final video...'
    task['progress'] = 96

    thread = threading.Thread(target=_run_pro_assemble, args=(task_id, fps), daemon=True)
    thread.start()

    return jsonify({'status': 'assembling'})


# --- Image upscale endpoints ---

@app.route('/api/image/upload', methods=['POST'])
def image_upload():
    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({'error': 'No image file provided'}), 400

    allowed = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif'}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return jsonify({'error': f'Unsupported format: {ext}'}), 400

    task_id = 'img_' + uuid.uuid4().hex[:12]
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
    filename = f"{task_id}_{safe_name}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # Get image dimensions using ffprobe
    info = get_image_info(filepath)
    if not info:
        os.remove(filepath)
        return jsonify({'error': 'Could not read image file'}), 400

    tasks[task_id] = {
        'status': 'uploaded',
        'type': 'image',
        'input': filepath,
        'filename': file.filename,
        'info': info,
        'progress': 0,
        'message': 'Ready',
    }

    return jsonify({'task_id': task_id, 'info': info})


@app.route('/api/image/upscale', methods=['POST'])
def image_upscale():
    data = request.json or {}
    task_id = data.get('task_id')
    scale = int(data.get('scale', 4))

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    task['status'] = 'processing'
    task['progress'] = 10
    task['message'] = 'Starting upscale...'

    thread = threading.Thread(target=_run_image_upscale, args=(task_id, scale), daemon=True)
    thread.start()

    return jsonify({'status': 'processing'})


@app.route('/api/image/status/<task_id>')
def image_status(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    result = {k: task.get(k) for k in ('status', 'progress', 'message')}
    if task.get('output_width'):
        result['output_width'] = task['output_width']
        result['output_height'] = task['output_height']
    return jsonify(result)


@app.route('/api/image/download/<task_id>')
def image_download(task_id):
    task = tasks.get(task_id)
    if not task or task.get('status') != 'completed':
        return jsonify({'error': 'Not ready'}), 404

    output_path = task.get('output')
    if not output_path or not os.path.exists(output_path):
        return jsonify({'error': 'Output file missing'}), 404

    stem = Path(task['filename']).stem
    download_name = f"{stem}_upscaled.png"
    return send_file(output_path, as_attachment=True, download_name=download_name)


# --- Helpers ---

def _detect_encoder():
    """Find the best available H.264 encoder."""
    try:
        result = subprocess.run(
            [FFMPEG, '-encoders'], capture_output=True, text=True, timeout=10
        )
        encoders = result.stdout
        if 'libx264' in encoders:
            return 'libx264'
        if 'h264_videotoolbox' in encoders:
            return 'h264_videotoolbox'
        if 'libopenh264' in encoders:
            return 'libopenh264'
    except Exception:
        pass
    return 'libopenh264'


def _encoder_args(encoder):
    """Return codec-specific encoding arguments."""
    if encoder == 'libx264':
        return ['-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p']
    elif encoder == 'h264_videotoolbox':
        return ['-c:v', 'h264_videotoolbox', '-b:v', '20M']
    else:
        return ['-c:v', 'libopenh264', '-b:v', '20M', '-pix_fmt', 'yuv420p']


def get_video_info(filepath):
    try:
        cmd = [
            FFPROBE, '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)

        video_stream = next(
            (s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None
        )
        if not video_stream:
            return None

        fps_str = video_stream.get('r_frame_rate', '24/1')
        num, den = map(int, fps_str.split('/'))
        fps = round(num / den, 2) if den else 24

        duration = float(data.get('format', {}).get('duration', 0))
        if not duration:
            duration = float(video_stream.get('duration', 0))

        return {
            'width': int(video_stream['width']),
            'height': int(video_stream['height']),
            'duration': round(duration, 2),
            'fps': fps,
            'codec': video_stream.get('codec_name', 'unknown'),
            'size': int(data.get('format', {}).get('size', 0)),
        }
    except Exception:
        return None


def get_image_info(filepath):
    """Get image dimensions using ffmpeg (since ffprobe may not be in PATH)."""
    try:
        # Use ffmpeg -i to get image info from stderr
        cmd = [FFMPEG, '-i', filepath, '-f', 'null', '-']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        stderr = result.stderr

        # Parse resolution from ffmpeg output like "64x64"
        match = re.search(r'(\d{2,5})x(\d{2,5})', stderr)
        if not match:
            return None

        width = int(match.group(1))
        height = int(match.group(2))

        # Detect format from extension
        ext = Path(filepath).suffix.lower().lstrip('.')
        fmt = ext.upper()
        if fmt in ('JPG',):
            fmt = 'JPEG'

        return {
            'width': width,
            'height': height,
            'format': fmt,
            'size': os.path.getsize(filepath),
        }
    except Exception:
        return None


def _run_image_upscale(task_id, scale):
    """Server-side image upscale using FFmpeg Lanczos + CAS + unsharp."""
    task = tasks[task_id]
    input_path = task['input']
    info = task['info']

    target_w = info['width'] * scale
    target_h = info['height'] * scale
    # Cap at 8K
    if target_w > 7680:
        ratio = 7680 / target_w
        target_w = 7680
        target_h = int(info['height'] * scale * ratio)
    target_h = target_h - (target_h % 2)
    target_w = target_w - (target_w % 2)

    output_filename = f"{task_id}_upscaled.png"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    vf = (
        f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
        f"cas=strength=0.4,"
        f"unsharp=5:5:0.6:5:5:0.3"
    )

    cmd = [
        FFMPEG, '-y', '-i', input_path,
        '-vf', vf,
        output_path
    ]

    try:
        task['progress'] = 30
        task['message'] = 'Upscaling with FFmpeg...'

        print(f"[image-upscale] Running: {' '.join(cmd)}", flush=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode == 0 and os.path.exists(output_path):
            task['status'] = 'completed'
            task['progress'] = 100
            task['output'] = output_path
            task['message'] = 'Done!'
            task['output_width'] = target_w
            task['output_height'] = target_h
        else:
            print(f"[image-upscale] FFmpeg error: {result.stderr[:500]}", flush=True)
            task['status'] = 'error'
            task['message'] = f'FFmpeg error: {result.stderr[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)


def _run_basic_upscale(task_id, scale):
    task = tasks[task_id]
    input_path = task['input']
    info = task['info']

    target_w = info['width'] * scale
    target_h = info['height'] * scale
    if target_w > 3840:
        ratio = 3840 / target_w
        target_w = 3840
        target_h = int(info['height'] * scale * ratio)
    target_h = target_h - (target_h % 2)
    target_w = target_w - (target_w % 2)

    total_frames = task['total_frames']
    output_filename = f"{task_id}_basic_4k.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    vf = (
        f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int,"
        f"cas=strength=0.4,"
        f"unsharp=5:5:0.6:5:5:0.3"
    )

    encoder = _detect_encoder()
    enc_args = _encoder_args(encoder)

    cmd = [
        FFMPEG, '-y', '-i', input_path,
        '-vf', vf,
        *enc_args,
        '-c:a', 'aac', '-b:a', '256k',
        '-progress', 'pipe:1',
        output_path
    ]

    try:
        print(f"[upscale] Running: {' '.join(cmd)}", flush=True)

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
        )

        for line in process.stdout:
            line = line.strip()
            if line.startswith('frame='):
                try:
                    frame = int(line.split('=')[1])
                    task['current_frame'] = frame
                    task['progress'] = min(99, int(frame / max(total_frames, 1) * 100))
                    task['message'] = f'Encoding frame {frame}/{total_frames}'
                except ValueError:
                    pass

        process.wait()

        if process.returncode == 0 and os.path.exists(output_path):
            task['status'] = 'completed'
            task['progress'] = 100
            task['output'] = output_path
            task['message'] = 'Done!'

            out_info = get_video_info(output_path)
            if out_info:
                task['output_info'] = out_info
        else:
            stderr = process.stderr.read() if process.stderr else 'Unknown error'
            print(f"[upscale] FFmpeg error (rc={process.returncode}): {stderr[-500:]}", flush=True)
            task['status'] = 'error'
            task['message'] = f'FFmpeg error: {stderr[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)


def _run_pro_assemble(task_id, fps):
    task = tasks[task_id]
    frames_dir = task['frames_dir']
    input_path = task['input']
    output_filename = f"{task_id}_pro_4k.mp4"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    encoder = _detect_encoder()
    enc_args = _encoder_args(encoder)

    cmd = [
        FFMPEG, '-y',
        '-framerate', str(fps),
        '-i', os.path.join(frames_dir, 'frame_%05d.jpg'),
        '-i', input_path,
        '-map', '0:v', '-map', '1:a?',
        *enc_args,
        '-c:a', 'aac', '-b:a', '256k',
        '-shortest',
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0 and os.path.exists(output_path):
            task['status'] = 'completed'
            task['progress'] = 100
            task['output'] = output_path
            task['message'] = 'Done!'

            out_info = get_video_info(output_path)
            if out_info:
                task['output_info'] = out_info
        else:
            task['status'] = 'error'
            task['message'] = f'Assembly error: {result.stderr[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)


if __name__ == '__main__':
    for folder in ('uploads', 'outputs', 'frames'):
        os.makedirs(folder, exist_ok=True)
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
