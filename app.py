#!/usr/bin/env python3
"""Upscale4K — AI-powered video upscaling server."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

import paths

VERSION = '1.0.0'

try:
    import ai_engine
    SERVER_AI = ai_engine.available()
except ImportError:
    ai_engine = None
    SERVER_AI = False

try:
    import face_restore as _face_restore
    FACE_RESTORE = SERVER_AI and _face_restore.available()
except Exception:
    FACE_RESTORE = False

def _find_bin(name):
    """Find a binary next to the packaged executable, in PATH, or in
    common install locations."""
    exe_name = name + '.exe' if sys.platform == 'win32' else name
    if paths.EXE_DIR:
        p = os.path.join(paths.EXE_DIR, exe_name)
        if os.path.isfile(p):
            return p
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


def _bin_works(path):
    try:
        return subprocess.run([path, '-version'], capture_output=True,
                              timeout=8).returncode == 0
    except Exception:
        return False


FFMPEG_OK = _bin_works(FFMPEG)
if not FFMPEG_OK:
    print(f'[startup] WARNING: ffmpeg not working at {FFMPEG} — '
          'video and Quality-mode features will be unavailable', flush=True)

app = Flask(__name__,
            template_folder=paths.resource('templates'),
            static_folder=paths.resource('static'))
app.config['UPLOAD_FOLDER'] = paths.data('uploads')
app.config['OUTPUT_FOLDER'] = paths.data('outputs')
app.config['FRAMES_FOLDER'] = paths.data('frames')
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB

tasks = {}
# Live subprocess handles per task, so cancel can kill them (not persisted)
_procs = {}


class TaskCancelled(Exception):
    pass


TASK_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')

DB_PATH = paths.data('tasks.db')


def _safe_task_id(task_id):
    """Validate task_id format to prevent path traversal in disk lookups."""
    return bool(TASK_ID_RE.fullmatch(task_id))


# --- Task persistence (SQLite) ---

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        'CREATE TABLE IF NOT EXISTS tasks '
        '(task_id TEXT PRIMARY KEY, data TEXT, updated REAL)'
    )
    return conn


def save_task(task_id):
    """Persist a task's JSON-serializable fields so it survives restarts."""
    task = tasks.get(task_id)
    if not task:
        return
    data = json.dumps({
        k: v for k, v in task.items()
        if isinstance(v, (str, int, float, bool, list, dict, type(None)))
    })
    try:
        with _db() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)',
                (task_id, data, time.time()),
            )
    except Exception as e:
        print(f'[db] save failed for {task_id}: {e}', flush=True)


def delete_task(task_id):
    tasks.pop(task_id, None)
    try:
        with _db() as conn:
            conn.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    except Exception:
        pass


def load_tasks():
    try:
        with _db() as conn:
            for tid, data in conn.execute('SELECT task_id, data FROM tasks'):
                try:
                    task = json.loads(data)
                except ValueError:
                    continue
                # Anything mid-flight when the server died can't resume
                if task.get('status') in ('processing', 'pro_processing'):
                    task['status'] = 'error'
                    task['message'] = 'Interrupted by server restart'
                tasks[tid] = task
        print(f'[db] loaded {len(tasks)} task(s)', flush=True)
    except Exception as e:
        print(f'[db] load failed: {e}', flush=True)


# --- Auto-cleanup of old files ---

CLEANUP_HOURS = float(os.environ.get('CLEANUP_HOURS', 24))


def _cleanup_loop():
    while True:
        cutoff = time.time() - CLEANUP_HOURS * 3600
        for folder in (app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER'],
                       app.config['FRAMES_FOLDER']):
            try:
                for entry in os.scandir(folder):
                    if entry.stat().st_mtime < cutoff:
                        if entry.is_dir():
                            shutil.rmtree(entry.path, ignore_errors=True)
                        else:
                            os.remove(entry.path)
            except OSError:
                pass
        try:
            with _db() as conn:
                old = [r[0] for r in conn.execute(
                    'SELECT task_id FROM tasks WHERE updated < ?', (cutoff,))]
            for tid in old:
                delete_task(tid)
        except Exception:
            pass
        time.sleep(3600)


@app.route('/')
def index():
    model_exists = paths.find_model('realesr-general-x4v3.onnx') is not None
    return render_template('index.html', model_available=model_exists,
                           server_ai=SERVER_AI, face_restore=FACE_RESTORE)


@app.route('/api/capabilities')
def capabilities():
    return jsonify({'server_ai': SERVER_AI, 'face_restore': FACE_RESTORE,
                    'ffmpeg': FFMPEG_OK, 'version': VERSION})


# Domains the About dialog may open in the system browser. The desktop
# webview can't open external links itself, so it asks the local server.
OPEN_ALLOWED = {'matily.org', 'www.matily.org', 'github.com'}


@app.route('/api/open', methods=['POST'])
def open_external():
    from urllib.parse import urlparse
    import webbrowser
    url = (request.json or {}).get('url', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in OPEN_ALLOWED:
        return jsonify({'error': 'URL not allowed'}), 400
    # Only honor requests from this machine — remote LAN users have a real
    # browser and don't need (or want) links opening on the server.
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify({'error': 'Local requests only'}), 403
    webbrowser.open(url)
    return jsonify({'status': 'ok'})


@app.route('/api/update-check')
def update_check():
    """Compare the running version against the newest GitHub release."""
    import ssl
    import urllib.request
    try:
        ctx = None
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        req = urllib.request.Request(
            'https://api.github.com/repos/riponcm/nextgenUp/releases/latest',
            headers={'Accept': 'application/vnd.github+json',
                     'User-Agent': f'NextGenUp/{VERSION}'})
        try:
            with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
                release = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:  # no releases published yet
                return jsonify({'current': VERSION, 'latest': VERSION,
                                'update_available': False,
                                'url': 'https://github.com/riponcm/nextgenUp/releases'})
            raise
        latest = release.get('tag_name', '').lstrip('v')
        if not latest:
            return jsonify({'error': 'No release found'}), 502

        def ver(v):
            nums = re.findall(r'\d+', v)[:3]
            return tuple(int(n) for n in nums) if nums else (0,)

        return jsonify({
            'current': VERSION,
            'latest': latest,
            'update_available': ver(latest) > ver(VERSION),
            'url': release.get('html_url',
                               'https://github.com/riponcm/nextgenUp/releases/latest'),
        })
    except Exception:
        return jsonify({'error': 'Could not reach GitHub'}), 502


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
    save_task(task_id)

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
            if task['status'] in ('completed', 'error', 'cancelled'):
                break
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/cancel/<task_id>', methods=['POST'])
@app.route('/api/image/cancel/<task_id>', methods=['POST'])
def cancel_task(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    task['cancel_requested'] = True
    proc = _procs.get(task_id)
    if proc and proc.poll() is None:
        proc.kill()
    return jsonify({'status': 'cancelling'})


@app.route('/api/download/<task_id>')
def download(task_id):
    task = tasks.get(task_id)

    if task and task.get('status') == 'completed':
        output_path = task.get('output')
        if output_path and os.path.exists(output_path):
            stem = Path(task['filename']).stem
            download_name = f"{stem}_4K.mp4"
            return send_file(output_path, as_attachment=True, download_name=download_name)

    # Fallback: task lost after server restart — find the file on disk
    if _safe_task_id(task_id):
        for suffix in ('_basic_4k.mp4', '_pro_4k.mp4'):
            path = os.path.join(app.config['OUTPUT_FOLDER'], task_id + suffix)
            if os.path.exists(path):
                return send_file(path, as_attachment=True, download_name=f"{task_id}_4K.mp4")

    return jsonify({'error': 'Not ready'}), 404


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
    save_task(task_id)

    return jsonify({'task_id': task_id, 'info': info})


@app.route('/api/image/upscale', methods=['POST'])
def image_upscale():
    data = request.json or {}
    task_id = data.get('task_id')
    scale = int(data.get('scale', 4))
    mode = data.get('mode', 'ffmpeg')  # ffmpeg | ai | ai-enhance

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    if mode in ('ai', 'ai-enhance') and not SERVER_AI:
        return jsonify({'error': 'Server AI not available on this host'}), 400

    task['status'] = 'processing'
    task['progress'] = 10
    task['message'] = 'Starting upscale...'

    if mode in ('ai', 'ai-enhance'):
        face = bool(data.get('face_restore')) and FACE_RESTORE
        target = _run_image_upscale_ai
        args = (task_id, scale, mode == 'ai-enhance', face)
    else:
        target = _run_image_upscale
        args = (task_id, scale)

    thread = threading.Thread(target=target, args=args, daemon=True)
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

    if task and task.get('status') == 'completed':
        output_path = task.get('output')
        if output_path and os.path.exists(output_path):
            stem = Path(task['filename']).stem
            download_name = f"{stem}_upscaled.png"
            return send_file(output_path, as_attachment=True, download_name=download_name)

    # Fallback: find file on disk after server restart
    if _safe_task_id(task_id):
        path = os.path.join(app.config['OUTPUT_FOLDER'], task_id + '_upscaled.png')
        if os.path.exists(path):
            return send_file(path, as_attachment=True, download_name=f"{task_id}_upscaled.png")

    return jsonify({'error': 'Not ready'}), 404


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


def _video_info_via_ffmpeg(filepath):
    """Fallback: parse `ffmpeg -i` stderr when ffprobe is unavailable."""
    try:
        result = subprocess.run([FFMPEG, '-hide_banner', '-i', filepath],
                                capture_output=True, text=True, timeout=20)
        err = result.stderr

        video_line = next((l for l in err.splitlines() if 'Video:' in l), None)
        if not video_line:
            return None
        res = re.search(r'(\d{2,5})x(\d{2,5})', video_line)
        if not res:
            return None
        codec = re.search(r'Video:\s*(\w+)', video_line)
        fps = re.search(r'([\d.]+)\s*fps', video_line)
        dur = re.search(r'Duration:\s*(\d+):(\d+):([\d.]+)', err)
        duration = 0.0
        if dur:
            duration = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))

        return {
            'width': int(res.group(1)),
            'height': int(res.group(2)),
            'duration': round(duration, 2),
            'fps': round(float(fps.group(1)), 2) if fps else 24,
            'codec': codec.group(1) if codec else 'unknown',
            'size': os.path.getsize(filepath),
        }
    except Exception:
        return None


def get_video_info(filepath):
    info = _video_info_via_ffprobe(filepath)
    if info is None:
        info = _video_info_via_ffmpeg(filepath)
    return info


def _video_info_via_ffprobe(filepath):
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
    """Get image dimensions — Pillow first, ffmpeg output as fallback."""
    width = height = None
    try:
        from PIL import Image as _Image
        with _Image.open(filepath) as im:
            width, height = im.size
    except Exception:
        try:
            cmd = [FFMPEG, '-i', filepath, '-f', 'null', '-']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            match = re.search(r'(\d{2,5})x(\d{2,5})', result.stderr)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
        except Exception:
            pass

    if not width or not height:
        return None

    ext = Path(filepath).suffix.lower().lstrip('.')
    fmt = 'JPEG' if ext == 'jpg' else ext.upper()

    return {
        'width': width,
        'height': height,
        'format': fmt,
        'size': os.path.getsize(filepath),
    }


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
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        _procs[task_id] = process
        try:
            _, stderr_out = process.communicate(timeout=120)
        finally:
            _procs.pop(task_id, None)

        class _R:  # keep the shape the branches below expect
            returncode = process.returncode
            stderr = stderr_out
        result = _R()

        if task.get('cancel_requested'):
            task['status'] = 'cancelled'
            task['message'] = 'Cancelled'
            save_task(task_id)
            return

        if result.returncode == 0 and os.path.exists(output_path):
            task['status'] = 'completed'
            task['progress'] = 100
            task['output'] = output_path
            task['message'] = 'Done!'
            task['output_width'] = target_w
            task['output_height'] = target_h
        else:
            print(f"[image-upscale] FFmpeg error: {result.stderr[-500:]}", flush=True)
            task['status'] = 'error'
            err_lines = [l for l in result.stderr.strip().splitlines() if l.strip() and not l.startswith(' ')]
            err_msg = err_lines[-1] if err_lines else result.stderr[-200:]
            task['message'] = f'FFmpeg error: {err_msg[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)
    save_task(task_id)


def _run_image_upscale_ai(task_id, scale, enhance=False, face=False):
    """Server-side Real-ESRGAN upscale (or same-size enhance) via ONNX Runtime."""
    task = tasks[task_id]
    input_path = task['input']

    output_filename = f"{task_id}_upscaled.png"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    def on_tile(done, total):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        # Leave headroom for the face-restoration pass at the end
        cap = 85 if face else 95
        task['progress'] = min(cap, 10 + int(done / total * (cap - 10)))
        task['message'] = f'AI {"enhancing" if enhance else "upscaling"} tile {done}/{total}'

    try:
        task['message'] = 'Running AI model on server...'
        if enhance:
            out_w, out_h = ai_engine.enhance_image(
                input_path, output_path, progress_cb=on_tile, face_restore=face)
        else:
            out_w, out_h = ai_engine.upscale_image(
                input_path, output_path, scale=scale, progress_cb=on_tile,
                face_restore=face)

        task['status'] = 'completed'
        task['progress'] = 100
        task['output'] = output_path
        task['message'] = 'Done!'
        task['output_width'] = out_w
        task['output_height'] = out_h
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[image-ai] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'AI upscale error: {str(e)[:200]}'
    save_task(task_id)


def _run_basic_upscale(task_id, scale):
    task = tasks[task_id]
    input_path = task['input']
    info = task['info']

    target_w = info['width'] * scale
    target_h = info['height'] * scale
    # Cap the longer dimension at 3840 (4K) to stay within encoder limits
    max_dim = 3840
    if target_w > max_dim or target_h > max_dim:
        if target_w >= target_h:
            ratio = max_dim / target_w
            target_w = max_dim
            target_h = int(target_h * ratio)
        else:
            ratio = max_dim / target_h
            target_h = max_dim
            target_w = int(target_w * ratio)
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
        _procs[task_id] = process

        # Drain stderr in a background thread to prevent pipe deadlock.
        # Without this, stderr buffer (64KB) fills up on large videos,
        # FFmpeg blocks writing to it, and we block reading stdout → hang.
        stderr_chunks = []
        def _drain_stderr():
            for line in process.stderr:
                stderr_chunks.append(line)
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

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
        _procs.pop(task_id, None)
        stderr_thread.join(timeout=5)
        stderr_output = ''.join(stderr_chunks)

        if task.get('cancel_requested'):
            task['status'] = 'cancelled'
            task['message'] = 'Cancelled'
            save_task(task_id)
            return

        if process.returncode == 0 and os.path.exists(output_path):
            task['status'] = 'completed'
            task['progress'] = 100
            task['output'] = output_path
            task['message'] = 'Done!'

            out_info = get_video_info(output_path)
            if out_info:
                task['output_info'] = out_info
        else:
            print(f"[upscale] FFmpeg error (rc={process.returncode}): {stderr_output[-500:]}", flush=True)
            task['status'] = 'error'
            err_lines = [l for l in stderr_output.strip().splitlines() if l.strip() and not l.startswith(' ')]
            err_msg = err_lines[-1] if err_lines else stderr_output[-200:]
            task['message'] = f'FFmpeg error: {err_msg[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)
    save_task(task_id)


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
            err_lines = [l for l in result.stderr.strip().splitlines() if l.strip() and not l.startswith(' ')]
            err_msg = err_lines[-1] if err_lines else result.stderr[-200:]
            task['message'] = f'Assembly error: {err_msg[:200]}'
    except Exception as e:
        task['status'] = 'error'
        task['message'] = str(e)
    save_task(task_id)


if __name__ == '__main__':
    for folder in (app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER'],
                   app.config['FRAMES_FOLDER'], paths.data('models')):
        os.makedirs(folder, exist_ok=True)
    load_tasks()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    # Debug mode exposes the Werkzeug debugger (remote code execution) —
    # never enable it by default on 0.0.0.0. Opt in with FLASK_DEBUG=1.
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host='0.0.0.0', port=port)
