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

**Enhance** is the mode for "keep my 500x500 photo 500x500, but make it crystal clear" — the AI reconstructs detail at 4x, then a high-quality downscale back to the original size removes noise, JPEG artifacts, and blur.

Other niceties:
- Portrait, landscape, and square inputs handled automatically (longer side capped at 4K for video, 8K for images)
- Live progress with ETA via Server-Sent Events
- Side-by-side before/after preview
- Audio preserved on video upscales
- Encoder auto-detection: libx264 → h264_videotoolbox → libopenh264

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

Upload limit is 2GB (`MAX_CONTENT_LENGTH` in `app.py`).

## Project layout

```
app.py                     Flask server: upload, upscale, progress, download
templates/index.html       Dashboard (sidebar: Image Upscale / Video Upscale)
static/css/style.css       Dark theme UI
static/js/app.js           Video controller + tab navigation
static/js/image-app.js     Image controller (Quick / Quality / Enhance)
static/js/pro-upscaler.js  Web Worker wrapper
static/js/pro-worker.js    ONNX inference worker (tiling, WebGPU/WASM)
convert_model.py           Optional: convert official PyTorch weights to ONNX
setup.sh                   One-command setup
```

## Roadmap

- [ ] Server-side Real-ESRGAN / HAT for maximum-quality tier
- [ ] Face restoration (CodeFormer / GFPGAN)
- [ ] Batch processing
- [ ] Task persistence (survive server restarts)
- [ ] Docker image

## License

[MIT](LICENSE)
