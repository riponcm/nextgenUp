#!/usr/bin/env python3
"""Upscale4K — AI-powered video upscaling server."""

import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, render_template, request, send_file

import paths
import userfiles

VERSION = '1.3.0'

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

try:
    import bg_remove
except Exception:
    bg_remove = None

try:
    import model_store
    import restore_engine
except Exception as e:
    print(f'[startup] restore tools unavailable: {e}', flush=True)
    model_store = None
    restore_engine = None

try:
    import audio_engine
except Exception as e:
    print(f'[startup] audio tools unavailable: {e}', flush=True)
    audio_engine = None

try:
    import video_tools
except Exception as e:
    print(f'[startup] video tools unavailable: {e}', flush=True)
    video_tools = None

def _find_bin(name):
    """Find a binary next to the packaged executable, in PATH, or in
    common install locations."""
    exe_name = name + '.exe' if sys.platform == 'win32' else name
    if paths.EXE_DIR:
        # Linux packages ship 'nextgenup-ffmpeg' so they don't collide with
        # the distro's /usr/bin/ffmpeg (issue #7); macOS/Windows keep 'ffmpeg'
        for cand in ('nextgenup-' + exe_name, exe_name):
            p = os.path.join(paths.EXE_DIR, cand)
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

# Per-session token for file-writing endpoints. It is embedded in the page,
# so only same-origin scripts can read it, and it travels in a custom header,
# which forces a CORS preflight — a cross-origin page (e.g. a malicious
# website poking at localhost) can neither obtain nor send it.
API_TOKEN = secrets.token_hex(32)

# Set by the desktop shell. Saving to a folder on this computer only makes
# sense when the server runs on the user's own machine; self-hosted web mode
# keeps browser downloads.
DESKTOP = os.environ.get('NEXTGENUP_DESKTOP') == '1'


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


# --- Saved files, automatic saving and leftover files (desktop app) ---

ACTIVE_STATUSES = ('processing', 'pro_processing', 'assembling')

# Newest last; the page polls this to tell the user where a result went
_recent_saves = []
_autosave_seen = set()
_autosave_waiting = {}   # task id -> first time seen completed without output


def _active_ids():
    return {tid for tid, t in tasks.items() if t.get('status') in ACTIVE_STATUSES}


def _note_save(entry):
    _recent_saves.append(entry)
    del _recent_saves[:-50]


def _autosave_once():
    """Copy every newly finished result into the user's save folder."""
    settings = userfiles.load_settings()
    enabled = DESKTOP and settings['auto_save']
    for tid, task in list(tasks.items()):
        if tid in _autosave_seen or task.get('status') != 'completed':
            continue
        out = task.get('output')
        if not out:
            # Parent tasks (stem splitter, video tools) never have an output;
            # but a runner sets status a moment before the path, so wait
            first = _autosave_waiting.setdefault(tid, time.time())
            if time.time() - first > 5:
                _autosave_seen.add(tid)
                _autosave_waiting.pop(tid, None)
            continue
        if tid.startswith('zip_') or out.endswith('.zip'):
            _autosave_seen.add(tid)
            continue
        if not os.path.exists(out):
            continue
        _autosave_seen.add(tid)
        if not enabled:
            continue
        name = userfiles.result_name(task, out)
        try:
            os.makedirs(settings['save_dir'], exist_ok=True)
            dest = userfiles.unique_path(settings['save_dir'], name)
            shutil.copyfile(out, dest)
        except OSError as e:
            task['autosave_error'] = str(e)
            save_task(tid)
            _note_save({'id': tid, 'name': name, 'error': str(e), 'ts': time.time()})
            print(f'[autosave] {tid} failed: {e}', flush=True)
            continue
        task['saved'] = True
        task['saved_to'] = dest
        save_task(tid)
        _note_save({'id': tid, 'name': os.path.basename(dest), 'path': dest,
                    'ts': time.time()})


def _autosave_loop():
    while True:
        time.sleep(2)
        try:
            _autosave_once()
        except Exception as e:
            print(f'[autosave] loop error: {e}', flush=True)


@app.route('/api/settings', methods=['GET', 'POST'])
def settings_endpoint():
    if not _local_only():
        return jsonify({'error': 'Not authorized'}), 403
    if request.method == 'POST':
        if not _write_authorized():
            return jsonify({'error': 'Not authorized'}), 403
        data = request.json or {}
        cur, err = userfiles.update_settings(
            auto_save=data.get('auto_save'), save_dir=data.get('save_dir'))
        if err:
            return jsonify({'error': err}), 400
    else:
        cur = userfiles.load_settings()
    return jsonify({'desktop': DESKTOP, 'auto_save': cur['auto_save'],
                    'save_dir': cur['save_dir'],
                    'default_dir': userfiles.default_save_dir()})


@app.route('/api/saves/recent')
def saves_recent():
    if not _local_only():
        return jsonify({'error': 'Not authorized'}), 403
    try:
        since = float(request.args.get('since', 0))
    except ValueError:
        since = 0
    return jsonify({'now': time.time(),
                    'items': [e for e in _recent_saves if e['ts'] > since]})


@app.route('/api/jobs/active')
def jobs_active():
    if not _local_only():
        return jsonify({'error': 'Not authorized'}), 403
    return jsonify({'active': len(_active_ids())})


def _storage_buckets():
    return userfiles.classify(app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER'],
                              app.config['FRAMES_FOLDER'], tasks, _active_ids())


@app.route('/api/storage')
def storage_report():
    if not _local_only():
        return jsonify({'error': 'Not authorized'}), 403
    rep = userfiles.summarize(_storage_buckets())
    rep['active_jobs'] = len(_active_ids())
    return jsonify(rep)


@app.route('/api/storage/clean', methods=['POST'])
def storage_clean():
    if not _write_authorized():
        return jsonify({'error': 'Not authorized'}), 403
    include_unsaved = bool((request.json or {}).get('include_unsaved'))
    result = userfiles.clean(_storage_buckets(), include_unsaved)
    userfiles.remove_empty_dirs(app.config['UPLOAD_FOLDER'], app.config['FRAMES_FOLDER'],
                                app.config['OUTPUT_FOLDER'])
    return jsonify(result)


@app.route('/api/reveal', methods=['POST'])
def reveal_in_file_manager():
    """Show a saved result (or the save folder) in Finder / Explorer. Only
    paths inside the user's save folder are allowed."""
    if not _write_authorized():
        return jsonify({'error': 'Not authorized'}), 403
    path = (request.json or {}).get('path') or userfiles.load_settings()['save_dir']
    if not os.path.isabs(path) or not userfiles.within(path, userfiles.load_settings()['save_dir']):
        return jsonify({'error': 'Not allowed'}), 400
    if not os.path.exists(path):
        return jsonify({'error': 'Not found'}), 404
    try:
        userfiles.reveal(path)
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'status': 'ok'})


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
                           server_ai=SERVER_AI, face_restore=FACE_RESTORE,
                           api_token=API_TOKEN)


@app.route('/llms.txt')
def llms_txt():
    path = paths.resource('llms.txt')
    if os.path.exists(path):
        return send_file(path, mimetype='text/plain')
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/capabilities')
def capabilities():
    return jsonify({'server_ai': SERVER_AI, 'face_restore': FACE_RESTORE,
                    'ffmpeg': FFMPEG_OK, 'version': VERSION,
                    'bg_remove': bool(bg_remove and bg_remove.available())})


# Domains the About dialog may open in the system browser. The desktop
# webview can't open external links itself, so it asks the local server.
OPEN_ALLOWED = {'matily.org', 'www.matily.org', 'jharu.matily.org', 'github.com'}


@app.route('/api/open', methods=['POST'])
def open_external():
    import webbrowser
    url = (request.json or {}).get('url', '')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in OPEN_ALLOWED:
        return jsonify({'error': 'URL not allowed'}), 400
    # Local connection + session token; remote LAN users have a real
    # browser and don't need (or want) links opening on the server.
    if not _write_authorized():
        return jsonify({'error': 'Not authorized'}), 403
    webbrowser.open(url)
    return jsonify({'status': 'ok'})


def _local_only():
    return request.remote_addr in ('127.0.0.1', '::1')


def _origin_ok(origin):
    """Accept only our own page (any localhost origin) or the Tauri webview."""
    if origin in ('tauri://localhost', 'https://tauri.localhost',
                  'http://tauri.localhost'):
        return True
    parsed = urlparse(origin)
    return parsed.scheme in ('http', 'https') and \
        parsed.hostname in ('127.0.0.1', 'localhost', '::1')


def _write_authorized():
    """Guard for endpoints that write to user-chosen paths or open URLs:
    local connection + the page's session token + a sane Origin. A localhost
    connection alone is not proof the USER asked — a malicious website could
    fire cross-origin requests at 127.0.0.1 (GitHub issue #2)."""
    if not _local_only():
        return False
    if request.headers.get('X-NGU-Token') != API_TOKEN:
        return False
    origin = request.headers.get('Origin')
    if origin and not _origin_ok(origin):
        return False
    return True


def _find_output(task_id):
    """Locate a finished task's output file, surviving server restarts."""
    task = tasks.get(task_id)
    if task and task.get('output') and os.path.exists(task['output']):
        return task['output']
    if _safe_task_id(task_id):
        for suffix in ('_basic_4k.mp4', '_pro_4k.mp4', '_upscaled.png',
                       '_nobg.png', '_clean.png', '_restored.png',
                       '_erased.png', '.zip', '.wav', '_denoised.wav',
                       '.mp4', '.gif'):
            p = os.path.join(app.config['OUTPUT_FOLDER'], task_id + suffix)
            if os.path.exists(p):
                return p
    return None


@app.route('/api/save', methods=['POST'])
def save_to_path():
    """Copy a finished result to a user-chosen absolute path. Used by the
    desktop app after its native Save dialog (the webview has no download
    manager). Local requests with the session token only."""
    if not _write_authorized():
        return jsonify({'error': 'Not authorized'}), 403
    data = request.json or {}
    task_id = data.get('task_id', '')
    dest = data.get('path', '')
    src = _find_output(task_id)
    if not src:
        return jsonify({'error': 'Result not found'}), 404
    if not os.path.isabs(dest) or not os.path.isdir(os.path.dirname(dest)):
        return jsonify({'error': 'Invalid destination path'}), 400
    try:
        shutil.copyfile(src, dest)
        if tasks.get(task_id):
            tasks[task_id]['saved'] = True
            save_task(task_id)
        return jsonify({'status': 'ok', 'path': dest})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/save-blob', methods=['POST'])
def save_blob():
    """Write posted bytes (browser-side results: Quick/Enhance images,
    batch zips) to a user-chosen absolute path. Local requests with the
    session token only."""
    if not _write_authorized():
        return jsonify({'error': 'Not authorized'}), 403
    dest = request.args.get('path', '')
    if not os.path.isabs(dest) or not os.path.isdir(os.path.dirname(dest)):
        return jsonify({'error': 'Invalid destination path'}), 400
    if not request.data:
        return jsonify({'error': 'No data'}), 400
    try:
        with open(dest, 'wb') as f:
            f.write(request.data)
        return jsonify({'status': 'ok', 'path': dest})
    except OSError as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/diag')
def diagnostics():
    """Environment report for bug triage."""
    import platform as _pf
    enc = _detect_encoder()
    try:
        ffv = subprocess.run([FFMPEG, '-version'], capture_output=True,
                             text=True, encoding='utf-8', errors='replace', timeout=8).stdout.splitlines()[0]
    except Exception as e:
        ffv = f'unavailable: {e}'
    return jsonify({
        'version': VERSION,
        'platform': f'{_pf.system()} {_pf.release()} {_pf.machine()}',
        'python': sys.version.split()[0],
        'ffmpeg': ffv,
        'encoder': enc,
        'server_ai': SERVER_AI,
        'face_restore': FACE_RESTORE,
        'frozen': paths.FROZEN,
    })


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
            download_name = f"{stem}{task.get('name_suffix', '_upscaled')}.png"
            return send_file(output_path, as_attachment=True, download_name=download_name)

    # Fallback: find file on disk after server restart
    if _safe_task_id(task_id):
        for suffix in ('_upscaled.png', '_nobg.png', '_clean.png',
                       '_restored.png', '_erased.png'):
            path = os.path.join(app.config['OUTPUT_FOLDER'], task_id + suffix)
            if os.path.exists(path):
                return send_file(path, as_attachment=True, download_name=task_id + suffix)

    return jsonify({'error': 'Not ready'}), 404


# --- Restore tools: model management + denoise/deblur, colorize, erase ---

@app.route('/api/models/<key>', methods=['GET', 'POST'])
def models_endpoint(key):
    """GET: availability / download progress. POST: start download."""
    if not model_store or not model_store.valid(key):
        return jsonify({'error': 'Unknown model'}), 404
    if request.method == 'POST':
        model_store.start_download(key)
    return jsonify(model_store.status(key))


def _start_tool_thread(task_id, runner, *args):
    task = tasks[task_id]
    task['status'] = 'processing'
    task['progress'] = 10
    task['message'] = 'Starting...'
    threading.Thread(target=runner, args=(task_id,) + args, daemon=True).start()
    return jsonify({'status': 'processing'})


def _finish_tool_task(task_id, output_path, suffix, size):
    task = tasks[task_id]
    task['status'] = 'completed'
    task['progress'] = 100
    task['output'] = output_path
    task['name_suffix'] = suffix
    task['message'] = 'Done!'
    task['output_width'], task['output_height'] = size


@app.route('/api/clean', methods=['POST'])
def clean_start():
    data = request.json or {}
    task_id = data.get('task_id')
    mode = data.get('mode', 'denoise')

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if mode not in ('denoise', 'deblur'):
        return jsonify({'error': 'Invalid mode'}), 400
    if not (model_store and model_store.available(mode)):
        return jsonify({'error': 'Model not installed'}), 400
    return _start_tool_thread(task_id, _run_clean, mode)


def _run_clean(task_id, mode):
    task = tasks[task_id]
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{task_id}_clean.png')
    verb = 'Denoising' if mode == 'denoise' else 'Deblurring'

    def on_tile(done, total):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = min(95, 10 + int(done / total * 85))
        task['message'] = f'{verb} tile {done}/{total}'

    try:
        task['message'] = f'{verb} on server...'
        size = restore_engine.clean_image(mode, task['input'], output_path,
                                          progress_cb=on_tile)
        _finish_tool_task(task_id, output_path, '_clean', size)
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[clean] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'{verb} error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/restore', methods=['POST'])
def restore_start():
    data = request.json or {}
    task_id = data.get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    colorize = bool(data.get('colorize'))
    face = bool(data.get('face')) and FACE_RESTORE
    enhance = bool(data.get('enhance')) and SERVER_AI
    if colorize and not (model_store and model_store.available('colorize')):
        return jsonify({'error': 'Colorization model not installed'}), 400
    if not (colorize or face or enhance):
        return jsonify({'error': 'Pick at least one restore option'}), 400
    return _start_tool_thread(task_id, _run_restore, colorize, face, enhance)


def _run_restore(task_id, colorize, face, enhance):
    task = tasks[task_id]
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{task_id}_restored.png')

    def check_cancel():
        if task.get('cancel_requested'):
            raise TaskCancelled()

    def on_progress(pct, msg):
        check_cancel()
        task['progress'] = pct
        task['message'] = msg

    try:
        working = task['input']
        # Stage 1: detail enhancement (Real-ESRGAN at original size),
        # with GFPGAN folded in when faces are requested
        if enhance:
            def on_tile(done, total):
                check_cancel()
                task['progress'] = min(60, 10 + int(done / total * 50))
                task['message'] = f'Enhancing detail (tile {done}/{total})'
            ai_engine.enhance_image(working, output_path,
                                    progress_cb=on_tile, face_restore=face)
            working = output_path
        elif face:
            on_progress(25, 'Restoring faces...')
            import cv2
            import face_restore as fr
            bgr = cv2.imread(working)
            cv2.imwrite(output_path, fr.restore_faces(bgr))
            working = output_path
        # Stage 2: colorization (last — works on the cleaned luminance)
        if colorize:
            def on_col(pct, msg):
                check_cancel()
                task['progress'] = 65 + int(pct * 0.3)
                task['message'] = msg
            size = restore_engine.colorize_image(working, output_path,
                                                progress_cb=on_col)
        else:
            if working != output_path:
                shutil.copyfile(working, output_path)
            from PIL import Image as _Image
            with _Image.open(output_path) as im:
                size = im.size
        check_cancel()
        _finish_tool_task(task_id, output_path, '_restored', size)
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[restore] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Restore error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/erase', methods=['POST'])
def erase_start():
    """Object eraser: takes the current image and a white-on-black mask as
    multipart files, returns a task to poll."""
    if not (model_store and model_store.available('erase')):
        return jsonify({'error': 'Eraser model not installed'}), 400
    image = request.files.get('image')
    mask = request.files.get('mask')
    if not image or not mask:
        return jsonify({'error': 'Image and mask required'}), 400

    task_id = 'img_' + uuid.uuid4().hex[:12]
    img_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{task_id}_erase_src.png')
    mask_path = os.path.join(app.config['UPLOAD_FOLDER'], f'{task_id}_mask.png')
    image.save(img_path)
    mask.save(mask_path)

    tasks[task_id] = {
        'status': 'uploaded', 'type': 'image', 'input': img_path,
        'filename': image.filename or 'image.png',
        'progress': 0, 'message': 'Ready',
    }
    _start_tool_thread(task_id, _run_erase, mask_path)
    return jsonify({'task_id': task_id})


def _run_erase(task_id, mask_path):
    task = tasks[task_id]
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{task_id}_erased.png')

    def on_progress(pct, msg):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = pct
        task['message'] = msg

    try:
        size = restore_engine.inpaint_image(task['input'], mask_path,
                                            output_path, progress_cb=on_progress)
        _finish_tool_task(task_id, output_path, '_erased', size)
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[erase] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Erase error: {str(e)[:200]}'
    save_task(task_id)


# --- Background removal endpoints ---

@app.route('/api/bg/model', methods=['GET', 'POST'])
def bg_model():
    """GET: model availability / download progress. POST: start download."""
    if not bg_remove:
        return jsonify({'error': 'Background removal module missing'}), 500
    if request.method == 'POST':
        bg_remove.start_download()
    return jsonify(bg_remove.download_status())


@app.route('/api/bg/remove', methods=['POST'])
def bg_remove_start():
    data = request.json or {}
    task_id = data.get('task_id')
    background = data.get('background', 'transparent')

    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if not (bg_remove and bg_remove.available()):
        return jsonify({'error': 'Background removal model not installed'}), 400
    if background != 'transparent' and not re.fullmatch(r'#[0-9a-fA-F]{6}', background):
        return jsonify({'error': 'Invalid background color'}), 400

    task['status'] = 'processing'
    task['progress'] = 10
    task['message'] = 'Starting background removal...'

    thread = threading.Thread(target=_run_bg_remove,
                              args=(task_id, background), daemon=True)
    thread.start()
    return jsonify({'status': 'processing'})


def _run_bg_remove(task_id, background):
    task = tasks[task_id]
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{task_id}_nobg.png')

    def on_progress(pct, msg):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = pct
        task['message'] = msg

    # Inference is one opaque model call (~seconds to ~30s on old CPUs) —
    # tick the bar forward so it doesn't look frozen mid-run.
    done_evt = threading.Event()

    def _tick():
        while not done_evt.wait(1.0):
            if task.get('status') == 'processing' and 30 <= task['progress'] < 80:
                task['progress'] += 2

    threading.Thread(target=_tick, daemon=True).start()
    try:
        out_w, out_h = bg_remove.remove_background(
            task['input'], output_path, background=background,
            progress_cb=on_progress)
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['status'] = 'completed'
        task['progress'] = 100
        task['output'] = output_path
        task['name_suffix'] = '_nobg'
        task['message'] = 'Done!'
        task['output_width'] = out_w
        task['output_height'] = out_h
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[bg-remove] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Background removal error: {str(e)[:200]}'
    finally:
        done_evt.set()
    save_task(task_id)


# --- Audio tools: vocal separation + noise removal ---

AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.opus',
              '.wma', '.aiff', '.aif', '.mp4', '.webm'}


@app.route('/api/audio/upload', methods=['POST'])
def audio_upload():
    if not audio_engine:
        return jsonify({'error': 'Audio tools unavailable'}), 500
    file = request.files.get('audio')
    if not file or not file.filename:
        return jsonify({'error': 'No audio file provided'}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS:
        return jsonify({'error': f'Unsupported format: {ext}'}), 400

    task_id = 'aud_' + uuid.uuid4().hex[:12]
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], f'{task_id}_{safe_name}')
    file.save(filepath)

    duration = audio_engine.probe_duration(filepath)
    if not duration:
        os.remove(filepath)
        return jsonify({'error': 'Could not read audio file'}), 400

    tasks[task_id] = {
        'status': 'uploaded', 'type': 'audio', 'input': filepath,
        'filename': file.filename, 'duration': round(duration, 1),
        'progress': 0, 'message': 'Ready',
    }
    save_task(task_id)
    return jsonify({'task_id': task_id, 'duration': round(duration, 1)})


@app.route('/api/audio/separate', methods=['POST'])
def audio_separate():
    task_id = (request.json or {}).get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if not (model_store and model_store.available('separate')):
        return jsonify({'error': 'Separation model not installed'}), 400
    return _start_tool_thread(task_id, _run_separate)


def _run_separate(task_id):
    task = tasks[task_id]
    stem = Path(task['filename']).stem
    voc_id, ins_id = task_id + '_voc', task_id + '_ins'
    voc_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{voc_id}.wav')
    ins_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{ins_id}.wav')

    def on_seg(done, total):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = min(95, 5 + int(done / total * 90))
        task['message'] = f'Separating segment {done}/{total}'

    try:
        task['message'] = 'Loading audio...'
        audio_engine.separate(task['input'], voc_path, ins_path,
                              progress_cb=on_seg)
        # Child task entries so download/save/zip machinery works per stem
        for cid, path, name in ((voc_id, voc_path, f'{stem}_vocals.wav'),
                                (ins_id, ins_path, f'{stem}_instrumental.wav')):
            tasks[cid] = {'status': 'completed', 'output': path,
                          'filename': name, 'progress': 100, 'message': 'Done!'}
            save_task(cid)
        task['status'] = 'completed'
        task['progress'] = 100
        task['message'] = 'Done!'
        task['stems'] = {'vocals': voc_id, 'instrumental': ins_id}
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[separate] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Separation error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/audio/denoise', methods=['POST'])
def audio_denoise():
    task_id = (request.json or {}).get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if not (model_store and model_store.available('denoise_speech')):
        return jsonify({'error': 'Denoise model not installed'}), 400
    return _start_tool_thread(task_id, _run_audio_denoise)


def _run_audio_denoise(task_id):
    task = tasks[task_id]
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{task_id}_denoised.wav')

    def on_chunk(done, total):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = min(95, 5 + int(done / total * 90))
        task['message'] = f'Removing noise ({done}/{total})'

    try:
        task['message'] = 'Loading audio...'
        audio_engine.denoise(task['input'], out_path, progress_cb=on_chunk)
        task['status'] = 'completed'
        task['progress'] = 100
        task['output'] = out_path
        task['message'] = 'Done!'
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[denoise-audio] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Denoise error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/audio/download/<task_id>')
def audio_download(task_id):
    task = tasks.get(task_id)
    if task and task.get('status') == 'completed' and task.get('output') \
            and os.path.exists(task['output']):
        stem = Path(task['filename']).stem
        name = task['filename'] if task['filename'].endswith('.wav') \
            else f'{stem}_denoised.wav'
        return send_file(task['output'], as_attachment=True, download_name=name)
    if _safe_task_id(task_id):
        for suffix in ('.wav', '_denoised.wav'):
            p = os.path.join(app.config['OUTPUT_FOLDER'], task_id + suffix)
            if os.path.exists(p):
                return send_file(p, as_attachment=True,
                                 download_name=task_id + suffix)
    return jsonify({'error': 'Not ready'}), 404


# --- Video tools: audio cleanup, compression, GIF ---

def _register_result(child_id, path, name):
    tasks[child_id] = {'status': 'completed', 'output': path,
                       'filename': name, 'progress': 100, 'message': 'Done!'}
    save_task(child_id)


def _video_tool_progress(task):
    def cb(pct, msg):
        if task.get('cancel_requested'):
            raise TaskCancelled()
        task['progress'] = min(98, pct)
        task['message'] = msg
    return cb


@app.route('/api/video/audio', methods=['POST'])
def video_audio():
    data = request.json or {}
    task_id = data.get('task_id')
    mode = data.get('mode', 'denoise')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    if mode not in ('denoise', 'remove_music', 'remove_voice'):
        return jsonify({'error': 'Invalid mode'}), 400
    model_key = 'denoise_speech' if mode == 'denoise' else 'separate'
    if not (video_tools and model_store and model_store.available(model_key)):
        return jsonify({'error': 'Model not installed'}), 400
    return _start_tool_thread(task_id, _run_video_audio, mode)


def _run_video_audio(task_id, mode):
    task = tasks[task_id]
    child_id = task_id + '_cln'
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], child_id + '.mp4')
    stem = Path(task['filename']).stem
    label = {'denoise': 'denoised', 'remove_music': 'voice_only',
             'remove_voice': 'music_only'}[mode]
    try:
        video_tools.clean_audio(task['input'], out_path, mode,
                                app.config['UPLOAD_FOLDER'],
                                progress_cb=_video_tool_progress(task))
        _register_result(child_id, out_path, f'{stem}_{label}.mp4')
        task['status'] = 'completed'
        task['progress'] = 100
        task['message'] = 'Done!'
        task['result_id'] = child_id
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[video-audio] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Audio cleanup error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/video/compress', methods=['POST'])
def video_compress():
    data = request.json or {}
    task_id = data.get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    try:
        target_mb = float(data.get('target_mb', 25))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid target size'}), 400
    if not 1 <= target_mb <= 2000:
        return jsonify({'error': 'Target size must be 1-2000 MB'}), 400
    if not video_tools:
        return jsonify({'error': 'Video tools unavailable'}), 500
    return _start_tool_thread(task_id, _run_video_compress, target_mb)


def _run_video_compress(task_id, target_mb):
    task = tasks[task_id]
    child_id = task_id + '_cmp'
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], child_id + '.mp4')
    stem = Path(task['filename']).stem
    try:
        duration = (task.get('info') or {}).get('duration') or \
            audio_engine.probe_duration(task['input'])
        video_tools.compress(task['input'], out_path, target_mb, duration,
                             app.config['UPLOAD_FOLDER'],
                             progress_cb=_video_tool_progress(task))
        size_mb = os.path.getsize(out_path) / 1e6
        _register_result(child_id, out_path, f'{stem}_compressed.mp4')
        task['status'] = 'completed'
        task['progress'] = 100
        task['message'] = f'Done! {size_mb:.1f} MB'
        task['result_id'] = child_id
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[video-compress] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'Compression error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/video/gif', methods=['POST'])
def video_gif():
    data = request.json or {}
    task_id = data.get('task_id')
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404
    try:
        fps = int(data.get('fps', 15))
        width = int(data.get('width', 480))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid options'}), 400
    if not (5 <= fps <= 30 and 120 <= width <= 1280):
        return jsonify({'error': 'fps must be 5-30, width 120-1280'}), 400
    if not video_tools:
        return jsonify({'error': 'Video tools unavailable'}), 500
    return _start_tool_thread(task_id, _run_video_gif, fps, width)


def _run_video_gif(task_id, fps, width):
    task = tasks[task_id]
    child_id = task_id + '_gif'
    out_path = os.path.join(app.config['OUTPUT_FOLDER'], child_id + '.gif')
    stem = Path(task['filename']).stem
    try:
        video_tools.to_gif(task['input'], out_path, fps, width,
                           app.config['UPLOAD_FOLDER'],
                           progress_cb=_video_tool_progress(task))
        size_mb = os.path.getsize(out_path) / 1e6
        _register_result(child_id, out_path, f'{stem}.gif')
        task['status'] = 'completed'
        task['progress'] = 100
        task['message'] = f'Done! {size_mb:.1f} MB'
        task['result_id'] = child_id
    except TaskCancelled:
        task['status'] = 'cancelled'
        task['message'] = 'Cancelled'
    except Exception as e:
        print(f'[video-gif] error: {e}', flush=True)
        task['status'] = 'error'
        task['message'] = f'GIF error: {str(e)[:200]}'
    save_task(task_id)


@app.route('/api/file/<task_id>')
def file_download(task_id):
    """Generic download for any completed result task."""
    task = tasks.get(task_id)
    if task and task.get('status') == 'completed' and task.get('output') \
            and os.path.exists(task['output']):
        return send_file(task['output'], as_attachment=True,
                         download_name=task.get('filename') or
                         os.path.basename(task['output']))
    src = _find_output(task_id)
    if src:
        return send_file(src, as_attachment=True,
                         download_name=os.path.basename(src))
    return jsonify({'error': 'Not ready'}), 404


# --- Batch zip: bundle finished results server-side (videos are far too
# large to zip in browser memory) ---

@app.route('/api/zip', methods=['POST'])
def make_zip():
    import zipfile
    data = request.json or {}
    task_ids = data.get('task_ids') or []
    if not isinstance(task_ids, list) or not task_ids or len(task_ids) > 200:
        return jsonify({'error': 'No tasks given'}), 400

    entries = []
    for tid in task_ids:
        if not isinstance(tid, str) or not _safe_task_id(tid):
            continue
        src = _find_output(tid)
        if not src:
            continue
        task = tasks.get(tid) or {}
        entries.append((src, task.get('filename') or '', Path(task.get('filename') or tid).stem))
    if not entries:
        return jsonify({'error': 'No finished results found'}), 404

    zip_id = 'zip_' + uuid.uuid4().hex[:12]
    zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_id + '.zip')
    try:
        used = set()
        # Videos and PNGs are already compressed — store, don't re-compress
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
            for src, orig_name, stem in entries:
                ext = Path(src).suffix
                if ext == '.wav':
                    # Audio results: the task filename is already the final
                    # name for stems; denoised outputs get their own suffix
                    name = orig_name if orig_name.endswith('.wav') \
                        else f'{stem}_denoised.wav'
                else:
                    suffix = '_nobg' if src.endswith('_nobg.png') else \
                        ('_4K' if ext == '.mp4' else '_upscaled')
                    name = f'{stem}{suffix}{ext}'
                base = Path(name).stem
                n = 1
                while name in used:
                    n += 1
                    name = f'{base}_{n}{ext}'
                used.add(name)
                zf.write(src, name)
    except OSError as e:
        return jsonify({'error': f'Zip failed: {e}'}), 500

    tasks[zip_id] = {'status': 'completed', 'output': zip_path,
                     'filename': zip_id + '.zip', 'progress': 100,
                     'message': 'Done!'}
    save_task(zip_id)
    return jsonify({'zip_id': zip_id, 'count': len(entries)})


@app.route('/api/zip/<zip_id>')
def zip_download(zip_id):
    src = _find_output(zip_id)
    if not src or not src.endswith('.zip'):
        return jsonify({'error': 'Not ready'}), 404
    return send_file(src, as_attachment=True, download_name='nextgenup_results.zip')


# --- Helpers ---

def _detect_encoder():
    """Find the best available H.264 encoder. On macOS prefer the
    VideoToolbox hardware encoder: it is roughly 10x faster than libx264
    for 4K output, which matters for long videos."""
    try:
        result = subprocess.run(
            [FFMPEG, '-encoders'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10
        )
        encoders = result.stdout
        if sys.platform == 'darwin' and 'h264_videotoolbox' in encoders:
            return 'h264_videotoolbox'
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
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
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
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
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
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=15)
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
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
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
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='replace'
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
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)

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
    if DESKTOP:
        # Nothing from a previous session can resume, so its uploaded copies
        # and extracted frames are only taking up space
        userfiles.purge_folder(app.config['UPLOAD_FOLDER'])
        userfiles.purge_folder(app.config['FRAMES_FOLDER'])
    # Results finished before this start are not saved retroactively
    _autosave_seen.update(tid for tid, t in tasks.items() if t.get('status') == 'completed')
    threading.Thread(target=_autosave_loop, daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    # Self-hosted web mode serves the LAN (0.0.0.0) as documented; the
    # desktop sidecar sets NEXTGENUP_HOST=127.0.0.1 so the packaged app
    # never listens on the network.
    host = os.environ.get('NEXTGENUP_HOST', '0.0.0.0')
    # Debug mode exposes the Werkzeug debugger (remote code execution) —
    # never enable it by default on 0.0.0.0. Opt in with FLASK_DEBUG=1.
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(debug=debug, host=host, port=port)
