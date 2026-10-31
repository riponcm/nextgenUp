"""User-facing file handling for the desktop app.

- where finished results are saved (one setting, applies to every tool)
- how a finished result is named
- a report of the leftover working files the app keeps in its data folder,
  and a cleaner for them

AI models are deliberately NOT part of any of this: the storage report and
the cleaner never look at the models folder.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import paths

SETTINGS_PATH = paths.data('settings.json')
_lock = threading.Lock()

# Files this young are never treated as leftovers: they belong to an upload
# the user just made or to a job that is writing right now.
RECENT_SECONDS = 10 * 60


# ----------------------------- settings -----------------------------

def documents_dir():
    """The user's Documents folder (follows OneDrive redirection on Windows)."""
    if sys.platform == 'win32':
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_PERSONAL = 5
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return buf.value
        except Exception:
            pass
    home = os.path.expanduser('~')
    docs = os.path.join(home, 'Documents')
    return docs if os.path.isdir(docs) else home


def default_save_dir():
    return os.path.join(documents_dir(), 'NextGenUp')


def load_settings():
    data = {}
    try:
        with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        pass
    if not isinstance(data, dict):
        data = {}
    save_dir = data.get('save_dir')
    if not isinstance(save_dir, str) or not os.path.isabs(save_dir):
        save_dir = default_save_dir()
    return {'auto_save': bool(data.get('auto_save', True)), 'save_dir': save_dir}


def update_settings(auto_save=None, save_dir=None):
    """Validate and store settings. Returns (settings, error_message)."""
    with _lock:
        cur = load_settings()
        if save_dir is not None:
            if not isinstance(save_dir, str) or not os.path.isabs(save_dir):
                return cur, 'The folder must be a full path'
            try:
                os.makedirs(save_dir, exist_ok=True)
                probe = os.path.join(save_dir, '.nextgenup-write-test')
                with open(probe, 'w') as f:
                    f.write('ok')
                os.remove(probe)
            except OSError as e:
                return cur, f'Cannot write to that folder: {e}'
            cur['save_dir'] = save_dir
        if auto_save is not None:
            cur['auto_save'] = bool(auto_save)
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(cur, f, indent=2)
        except OSError as e:
            return cur, f'Could not store the setting: {e}'
        return cur, None


# ------------------------------ naming ------------------------------

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name):
    name = _ILLEGAL.sub('_', name).strip().strip('.')
    return name or 'result'


def unique_path(folder, name):
    """A path inside folder that does not exist yet: name, name_2, name_3..."""
    name = safe_filename(name)
    stem, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f'{stem}_{n}{ext}')
    return path


def result_name(task, out_path):
    """The file name a finished result is saved under, matching what the
    Download buttons suggest."""
    base = os.path.basename(out_path)
    fname = task.get('filename') or ''
    # Child results (separated stems, video-tool outputs) already carry
    # their final display name
    if 'input' not in task and Path(fname).suffix:
        return fname
    stem = Path(fname).stem or 'result'
    ext = Path(out_path).suffix
    if base.endswith(('_basic_4k.mp4', '_pro_4k.mp4')):
        return f'{stem}_4K.mp4'
    if task.get('name_suffix'):
        return f"{stem}{task['name_suffix']}{ext}"
    if base.endswith('_upscaled.png'):
        return f'{stem}_upscaled.png'
    if ext == '.wav':
        return f'{stem}_denoised.wav'
    return f'{stem}_result{ext}'


# ------------------------- storage report -------------------------

def _owner(path, out_to_task):
    return out_to_task.get(os.path.normcase(os.path.abspath(path)))


def _iter_files(folder):
    """Every file under folder with its size and modification time."""
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            yield p, st.st_size, st.st_mtime


def classify(upload_dir, output_dir, frames_dir, tasks, active_ids):
    """Sort the working files into buckets.

    leftovers: safe to remove (inputs of old jobs, temp files, copies of
               results that are already saved elsewhere)
    unsaved:   finished results that were never saved to the user's folder
    protected: belongs to a running job, or is too new to judge
    """
    now = time.time()
    out_to_task = {}
    for tid, t in tasks.items():
        if t.get('output'):
            out_to_task[os.path.normcase(os.path.abspath(t['output']))] = (tid, t)

    buckets = {'leftovers': [], 'unsaved': [], 'protected': []}

    for folder in (upload_dir, frames_dir):
        for p, size, mtime in _iter_files(folder):
            rel = os.path.relpath(p, folder)
            first = rel.split(os.sep)[0]
            running = any(first.startswith(a) for a in active_ids)
            if running or now - mtime < RECENT_SECONDS:
                buckets['protected'].append((p, size))
            else:
                buckets['leftovers'].append((p, size))

    for p, size, mtime in _iter_files(output_dir):
        hit = _owner(p, out_to_task)
        if hit is None:
            # No task points at it any more: nothing can use it
            (buckets['protected'] if now - mtime < RECENT_SECONDS
             else buckets['leftovers']).append((p, size))
            continue
        tid, t = hit
        if tid in active_ids or t.get('status') != 'completed':
            buckets['protected'].append((p, size))
        elif t.get('saved'):
            buckets['leftovers'].append((p, size))
        else:
            buckets['unsaved'].append((p, size))
    return buckets


def summarize(buckets):
    return {k: {'files': len(v), 'bytes': sum(s for _, s in v)}
            for k, v in buckets.items()}


def clean(buckets, include_unsaved=False):
    """Delete the leftover bucket (and the unsaved one if asked)."""
    victims = list(buckets['leftovers'])
    if include_unsaved:
        victims += buckets['unsaved']
    freed = removed = 0
    for p, size in victims:
        try:
            os.remove(p)
            freed += size
            removed += 1
        except OSError:
            pass
    return {'files': removed, 'bytes': freed}


def remove_empty_dirs(*folders):
    for folder in folders:
        for root, dirs, files in os.walk(folder, topdown=False):
            if root != folder and not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass


def purge_folder(folder):
    """Delete everything inside folder (keeps the folder itself)."""
    import shutil
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                shutil.rmtree(e.path, ignore_errors=True)
            else:
                os.remove(e.path)
        except OSError:
            pass


# ----------------------- show in file manager -----------------------

def within(path, folder):
    try:
        p = os.path.realpath(path)
        f = os.path.realpath(folder)
    except OSError:
        return False
    return p == f or p.startswith(f + os.sep)


def reveal(path):
    """Open the folder (or select the file) in Finder / Explorer."""
    if sys.platform == 'darwin':
        cmd = ['open', path] if os.path.isdir(path) else ['open', '-R', path]
    elif sys.platform == 'win32':
        cmd = ['explorer', os.path.normpath(path)] if os.path.isdir(path) \
            else ['explorer', '/select,', os.path.normpath(path)]
    else:
        target = path if os.path.isdir(path) else os.path.dirname(path)
        cmd = ['xdg-open', target]
    subprocess.Popen(cmd)
