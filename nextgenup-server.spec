# PyInstaller spec — builds the Flask backend into a single sidecar binary.
# Bundles templates, static assets, and the small Real-ESRGAN + YuNet models.
# The 340MB GFPGAN model is deliberately NOT bundled; users drop it into the
# app-data models/ folder to enable face restoration.
import os

datas = [
    ('templates', 'templates'),
    ('static/css', 'static/css'),
    ('static/js', 'static/js'),
    ('static/favicon.png', 'static'),
]
for small_model in ('realesr-general-x4v3.onnx', 'face_detection_yunet_2023mar.onnx'):
    p = os.path.join('static', 'models', small_model)
    if os.path.exists(p):
        datas.append((p, 'static/models'))

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['ai_engine', 'face_restore', 'paths'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='nextgenup-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
