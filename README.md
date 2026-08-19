# Upscale4K

Open-source AI image & video upscaling that runs entirely on your own machine. Upload a low-res photo or video, get a crisp 4K version back — no cloud, no subscription, no watermarks.

**Video upscaling** (Basic FFmpeg / Pro AI) &nbsp;•&nbsp; **Image upscaling** (Quick AI / Quality FFmpeg) &nbsp;•&nbsp; **Image enhancement** (same size, crystal clear)

## Features

### Video Upscale
| Mode | Engine | Runs on |
|------|--------|---------|
| **Basic** | FFmpeg Lanczos + CAS sharpening + unsharp | Server (CPU) |
| **Pro** | Real-ESRGAN AI, frame by frame | Your browser GPU (WebGPU, WASM fallback) |

### Image Upscale
| Mode | Engine | Runs on |
|------|--------|---------|
| **Quick** | Real-ESRGAN AI (2x/4x) | Your browser GPU |
| **Quality** | FFmpeg Lanczos + CAS (2x/4x, up to 8K) | Server |
| **Enhance** | AI 4x upscale → downscale to original size | Your browser GPU |
| **Ultra** | Real-ESRGAN AI via ONNX Runtime | Server (any device, no WebGPU needed) |

**Enhance** is the mode for "keep my 500x500 photo 500x500, but make it crystal clear" — the AI reconstructs detail at 4x, then a high-quality downscale back to the original size removes noise, JPEG artifacts, and blur.

**Ultra** runs the same AI on the server, so phones, tablets, and browsers without WebGPU on your network get full AI quality.

### Face Restoration (optional)
GFPGAN v1.4 + YuNet face detection, running server-side through ONNX Runtime. Tick **Restore faces** in Ultra mode — every detected face is aligned, restored, and seamlessly blended back. Enable it by downloading the models via `setup.sh` (~340MB).

### Batch & Compare
- **Batch mode** — drop multiple images at once, run any mode over the whole set, download everything as a zip
- **Before/after slider** — drag a divider across the result to compare against the original

Other niceties:
- Portrait, landscape, and square inputs handled automatically (longer side capped at 4K for video, 8K for images)
- Live progress with ETA via Server-Sent Events
- Audio preserved on video upscales
- Encoder auto-detection: libx264 → h264_videotoolbox → libopenh264
- Tasks persist in SQLite — completed results survive server restarts
- Auto-cleanup deletes uploads/outputs older than 24h (`CLEANUP_HOURS` env var)

## Requirements

- **Python 3.10+**
- **FFmpeg** (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux)
- A Chromium browser (Chrome/Edge 113+) for the AI modes — they use WebGPU, with automatic WASM fallback

## Quick Start

```bash
git clone https://github.com/riponcm/nextgenUp.git
cd nextgenUp
./setup.sh          # creates venv, installs deps, downloads the 5MB ONNX model
source venv/bin/activate
python app.py
```

Open **http://localhost:5000**.

To use it from other devices on your network, find your machine's IP and open `http://<your-ip>:5000`. Note: WebGPU (the browser AI modes) requires HTTPS or localhost — on a LAN IP either use the server-side modes, or add the URL to `chrome://flags` → "Insecure origins treated as secure".

## How it works

```
Browser                              Flask server
───────                              ────────────
upload video/image  ──────────────►  ffprobe reads metadata
                                     
Basic/Quality mode  ──────────────►  FFmpeg: lanczos scale + CAS + unsharp
   progress ◄── SSE ──────────────   
                                     
Pro/Quick/Enhance mode               
   ONNX Runtime Web (WebGPU/WASM)    
   Real-ESRGAN in a Web Worker       
   tile-based inference (64–128px)   
   frames POSTed back ────────────►  FFmpeg assembles + muxes audio
```

The AI model is [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) (`realesr-general-x4v3`, SRVGGNetCompact, ~5MB ONNX) running client-side via [ONNX Runtime Web](https://onnxruntime.ai/). Inference happens in a Web Worker so the UI never freezes; images are processed in overlapping tiles to fit GPU memory.

## Configuration

| Env var | Default | Purpose |
|---------|---------|---------|
| `PORT` | `5000` | Server port |
| `FLASK_DEBUG` | `0` | Set to `1` for the Werkzeug debugger (never on an exposed host) |
| `CLEANUP_HOURS` | `24` | Files older than this are auto-deleted |

Upload limit is 2GB (`MAX_CONTENT_LENGTH` in `app.py`).

## Project layout

```
app.py                     Flask server: upload, upscale, progress, download
ai_engine.py               Server-side Real-ESRGAN (ONNX Runtime, tiled)
face_restore.py            GFPGAN face restoration + YuNet detection
templates/index.html       Dashboard (sidebar: Image Upscale / Video Upscale)
static/css/style.css       Dark theme UI
static/js/app.js           Video controller + tab navigation
static/js/image-app.js     Image controller (modes, batch, compare slider)
static/js/pro-upscaler.js  Web Worker wrapper
static/js/pro-worker.js    ONNX inference worker (tiling, WebGPU/WASM)
convert_model.py           Optional: convert official PyTorch weights to ONNX
setup.sh                   One-command setup
```

## Roadmap

- [ ] Bigger server models (Real-ESRGAN x4plus / HAT) for a maximum-quality tier
- [ ] Face restoration for video frames
- [ ] WebCodecs-based video frame extraction (faster Pro mode)
- [ ] Docker image

## License

[MIT](LICENSE)
