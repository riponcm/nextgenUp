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

echo ""
echo "=== Setup Complete ==="
echo "Start the app:  source venv/bin/activate && python app.py"
echo "Open dashboard:  http://localhost:5000"
