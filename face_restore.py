"""Face restoration via GFPGAN v1.4 (ONNX) + YuNet face detection.

Pipeline per face: detect 5 landmarks → similarity-align to the FFHQ 512
template → GFPGAN restore → inverse-warp and blend back with a feathered
mask. Runs entirely on CPU through ONNX Runtime / OpenCV.
"""

import os
import threading

import numpy as np

try:
    import cv2
    CV2_OK = True
except ImportError:
    CV2_OK = False

import onnxruntime as ort

GFPGAN_PATH = os.path.join('static', 'models', 'GFPGANv1.4.onnx')
YUNET_PATH = os.path.join('static', 'models', 'face_detection_yunet_2023mar.onnx')

# FFHQ 512x512 five-point template (left eye, right eye, nose, mouth corners)
FACE_TEMPLATE = np.array([
    [192.98138, 239.94708],
    [318.90277, 240.1936],
    [256.63416, 314.01935],
    [201.26117, 371.41043],
    [313.08905, 371.15118],
], dtype=np.float32)

_session = None
_lock = threading.Lock()


def available():
    return CV2_OK and os.path.exists(GFPGAN_PATH) and os.path.exists(YUNET_PATH)


def _get_session():
    global _session
    with _lock:
        if _session is None:
            _session = ort.InferenceSession(
                GFPGAN_PATH, providers=['CPUExecutionProvider']
            )
        return _session


def _detect_faces(bgr):
    """Return a list of 5x2 landmark arrays for each detected face."""
    h, w = bgr.shape[:2]
    # YuNet works best under ~1000px — detect on a downscaled copy
    det_scale = min(1.0, 1000.0 / max(h, w))
    dw, dh = int(w * det_scale), int(h * det_scale)
    small = cv2.resize(bgr, (dw, dh)) if det_scale < 1.0 else bgr

    detector = cv2.FaceDetectorYN.create(YUNET_PATH, '', (dw, dh), 0.7, 0.3, 5000)
    _, faces = detector.detect(small)
    if faces is None:
        return []

    out = []
    for f in faces:
        # f: [x, y, w, h, lm0x, lm0y, ... lm4x, lm4y, score]
        lm = f[4:14].reshape(5, 2) / det_scale
        # Normalize ordering: eyes sorted by x, mouth corners sorted by x
        eyes = sorted(lm[0:2].tolist(), key=lambda p: p[0])
        mouth = sorted(lm[3:5].tolist(), key=lambda p: p[0])
        out.append(np.array([eyes[0], eyes[1], lm[2].tolist(), mouth[0], mouth[1]],
                            dtype=np.float32))
    return out


def _restore_crop(rgb512):
    """Run GFPGAN on an aligned 512x512 RGB uint8 crop."""
    sess = _get_session()
    inp = rgb512.astype(np.float32) / 255.0
    inp = (inp - 0.5) / 0.5
    inp = inp.transpose(2, 0, 1)[np.newaxis]
    out = sess.run(None, {sess.get_inputs()[0].name: np.ascontiguousarray(inp)})[0]
    out = np.clip(out[0].transpose(1, 2, 0), -1.0, 1.0)
    return ((out + 1.0) * 127.5 + 0.5).astype(np.uint8)


def restore_faces(bgr, progress_cb=None):
    """Detect and restore every face in a BGR uint8 image. Returns BGR."""
    landmarks = _detect_faces(bgr)
    if not landmarks:
        return bgr

    result = bgr.copy()
    h, w = bgr.shape[:2]

    for i, lm in enumerate(landmarks):
        M, _ = cv2.estimateAffinePartial2D(lm, FACE_TEMPLATE, method=cv2.LMEDS)
        if M is None:
            continue

        crop = cv2.warpAffine(bgr, M, (512, 512), borderMode=cv2.BORDER_REFLECT)
        restored = _restore_crop(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        restored = cv2.cvtColor(restored, cv2.COLOR_RGB2BGR)

        # Feathered mask so the paste-back has no hard seam
        mask = np.ones((512, 512), dtype=np.float32)
        mask = cv2.erode(mask, np.ones((32, 32), np.float32))
        mask = cv2.GaussianBlur(mask, (41, 41), 0)

        inv = cv2.invertAffineTransform(M)
        pasted = cv2.warpAffine(restored, inv, (w, h))
        pasted_mask = cv2.warpAffine(mask, inv, (w, h))[..., np.newaxis]

        result = (pasted * pasted_mask +
                  result.astype(np.float32) * (1.0 - pasted_mask)).astype(np.uint8)

        if progress_cb:
            progress_cb(i + 1, len(landmarks))

    return result
