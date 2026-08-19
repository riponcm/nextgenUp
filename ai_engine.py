"""Server-side AI upscaling via ONNX Runtime (CPU).

Runs the same Real-ESRGAN model as the browser modes, but on the server —
so any device on the network can use AI upscaling without WebGPU, and
batch jobs don't strain the browser.
"""

import os
import threading

import numpy as np
import onnxruntime as ort
from PIL import Image

import paths

MODEL_PATH = paths.find_model('realesr-general-x4v3.onnx') or \
    os.path.join('static', 'models', 'realesr-general-x4v3.onnx')
MODEL_SCALE = 4
TILE = 128
PAD = 12

_session = None
_session_lock = threading.Lock()
# The ONNX session is not safe for concurrent run() calls with shared tensors;
# serialize inference so parallel requests queue instead of corrupting.
_infer_lock = threading.Lock()


def available():
    return os.path.exists(MODEL_PATH)


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            _session = ort.InferenceSession(
                MODEL_PATH, providers=['CPUExecutionProvider']
            )
        return _session


def upscale_array(arr, progress_cb=None):
    """Upscale an HxWx3 float32 [0,1] array by MODEL_SCALE using tiled inference."""
    sess = _get_session()
    input_name = sess.get_inputs()[0].name

    h, w = arr.shape[:2]
    out = np.empty((h * MODEL_SCALE, w * MODEL_SCALE, 3), dtype=np.float32)

    tiles_x = (w + TILE - 1) // TILE
    tiles_y = (h + TILE - 1) // TILE
    total = tiles_x * tiles_y
    done = 0

    for ty in range(tiles_y):
        for tx in range(tiles_x):
            sx, sy = tx * TILE, ty * TILE
            x0, y0 = max(0, sx - PAD), max(0, sy - PAD)
            x1, y1 = min(w, sx + TILE + PAD), min(h, sy + TILE + PAD)

            tile = arr[y0:y1, x0:x1]                      # HWC
            inp = tile.transpose(2, 0, 1)[np.newaxis]      # NCHW
            with _infer_lock:
                res = sess.run(None, {input_name: np.ascontiguousarray(inp)})[0]
            res = res[0].transpose(1, 2, 0)                # HWC, 4x tile size

            # Crop the padding off and paste the core region into the output
            pl = (sx - x0) * MODEL_SCALE
            pt = (sy - y0) * MODEL_SCALE
            cw = (min(w, sx + TILE) - sx) * MODEL_SCALE
            ch = (min(h, sy + TILE) - sy) * MODEL_SCALE
            out[sy * MODEL_SCALE: sy * MODEL_SCALE + ch,
                sx * MODEL_SCALE: sx * MODEL_SCALE + cw] = res[pt:pt + ch, pl:pl + cw]

            done += 1
            if progress_cb:
                progress_cb(done, total)

    return np.clip(out, 0.0, 1.0)


def _maybe_restore_faces(result, face_restore, progress_cb=None):
    """Optionally run GFPGAN face restoration on a PIL image."""
    if not face_restore:
        return result
    try:
        import face_restore as fr
        if not fr.available():
            return result
        if progress_cb:
            progress_cb('Restoring faces...')
        rgb = np.asarray(result)
        bgr = rgb[:, :, ::-1].copy()
        bgr = fr.restore_faces(bgr)
        return Image.fromarray(bgr[:, :, ::-1])
    except Exception as e:
        print(f'[face-restore] skipped: {e}', flush=True)
        return result


def upscale_image(in_path, out_path, scale=4, progress_cb=None, face_restore=False):
    """Upscale an image file. scale 4 = native; scale 2 = 4x then downsample.
    Returns (out_width, out_height)."""
    img = Image.open(in_path).convert('RGB')
    arr = np.asarray(img, dtype=np.float32) / 255.0

    out = upscale_array(arr, progress_cb=progress_cb)
    result = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))

    if scale == 2:
        result = result.resize((img.width * 2, img.height * 2), Image.LANCZOS)

    # Cap at 8K on the longer side
    max_dim = 7680
    if max(result.size) > max_dim:
        ratio = max_dim / max(result.size)
        result = result.resize(
            (int(result.width * ratio), int(result.height * ratio)), Image.LANCZOS
        )

    result = _maybe_restore_faces(result, face_restore)
    result.save(out_path, 'PNG')
    return result.size


def enhance_image(in_path, out_path, progress_cb=None, face_restore=False):
    """AI 4x upscale then downscale back to the original size (crystal-clear
    same-resolution output). Returns (out_width, out_height)."""
    img = Image.open(in_path).convert('RGB')
    arr = np.asarray(img, dtype=np.float32) / 255.0

    out = upscale_array(arr, progress_cb=progress_cb)
    result = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))
    # Restore faces at 4x, before downscaling — GFPGAN gets more pixels to work with
    result = _maybe_restore_faces(result, face_restore)
    result = result.resize(img.size, Image.LANCZOS)
    result.save(out_path, 'PNG')
    return result.size
