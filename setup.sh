#!/bin/bash
set -e

echo "=== Upscale4K Setup ==="

# Check FFmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "FFmpeg not found. Installing via Homebrew..."
    if command -v brew &> /dev/null; then
        brew install ffmpeg
    else
        echo "ERROR: FFmpeg is required. Install it manually: brew install ffmpeg"
        exit 1
    fi
else
    echo "FFmpeg found: $(ffmpeg -version | head -1)"
fi

# Create Python venv
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Installing Python dependencies..."
pip install -q -r requirements.txt

# Create directories
mkdir -p uploads outputs frames static/models

# Download pre-converted ONNX model (~5MB, needed for AI modes)
MODEL="static/models/realesr-general-x4v3.onnx"
if [ ! -f "$MODEL" ]; then
    echo "Downloading Real-ESRGAN ONNX model (~5MB)..."
    curl -L -o "$MODEL" \
        "https://huggingface.co/OwlMaster/AllFilesRope/resolve/main/realesr-general-x4v3.onnx"
    echo "Model downloaded."
fi

# Face restoration models (optional — GFPGAN is ~340MB)
YUNET="static/models/face_detection_yunet_2023mar.onnx"
GFPGAN="static/models/GFPGANv1.4.onnx"
if [ ! -f "$GFPGAN" ]; then
    read -p "Download face restoration models (GFPGAN, ~340MB)? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        [ -f "$YUNET" ] || curl -L -o "$YUNET" \
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
        curl -L -o "$GFPGAN" \
            "https://huggingface.co/OwlMaster/AllFilesRope/resolve/main/GFPGANv1.4.onnx"
        pip install -q opencv-python-headless
        echo "Face restoration ready."
    else
        echo "Skipping. Re-run setup.sh any time to add it."
    fi
fi

echo ""
echo "=== Setup Complete ==="
echo "Start the app:  source venv/bin/activate && python app.py"
echo "Open dashboard:  http://localhost:5000"
