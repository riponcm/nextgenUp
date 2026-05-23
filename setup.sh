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
mkdir -p uploads outputs static/models

# Convert ONNX model (optional — requires torch)
if [ ! -f "static/models/realesr-general-x4v3.onnx" ]; then
    echo ""
    echo "=== ONNX Model Setup (for Pro WebGPU mode) ==="
    read -p "Download and convert Real-ESRGAN model to ONNX? Requires torch (~2GB download). [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        pip install -q torch
        python convert_model.py
    else
        echo "Skipping model conversion. Pro mode will be unavailable."
        echo "Run 'python convert_model.py' later to enable it."
    fi
fi

echo ""
echo "=== Setup Complete ==="
echo "Start the app:  source venv/bin/activate && python app.py"
echo "Open dashboard:  http://localhost:5000"
