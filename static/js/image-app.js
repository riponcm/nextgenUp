/**
 * Upscale4K — Image upscaling controller.
 */
(function () {
    'use strict';

    // --- State ---
    const imgState = {
        taskId: null,
        imageInfo: null,
        selectedMode: 'quick',
        selectedScale: 4,
        processing: false,
        file: null,
    };

    // --- DOM refs ---
    const $ = (sel) => document.querySelector(sel);
    const imgDropzone = $('#img-dropzone');
    const imgFileInput = $('#img-file-input');
    const imgUploadSection = $('#img-upload-section');
    const imgAppSection = $('#img-app-section');
    const imgOriginal = $('#img-original');
    const imgUpscaled = $('#img-upscaled');
    const imgOutputPlaceholder = $('#img-output-placeholder');
    const imgUpscaleBtn = $('#img-upscale-btn');
    const imgProgressSection = $('#img-progress-section');
    const imgProgressFill = $('#img-progress-fill');
    const imgProgressMessage = $('#img-progress-message');
    const imgProgressPercent = $('#img-progress-percent');
    const imgProgressDetail = $('#img-progress-detail');
    const imgProgressEta = $('#img-progress-eta');
    const imgDownloadSection = $('#img-download-section');
    const imgDownloadBtn = $('#img-download-btn');
    const imgDownloadDetails = $('#img-download-details');
    const imgNewRow = $('#img-new-row');
    const imgNewBtn = $('#img-new-btn');

    // --- Init ---
    bindImageEvents();

    function bindImageEvents() {
        // Dropzone
        imgDropzone.addEventListener('click', () => imgFileInput.click());
        imgDropzone.addEventListener('dragover', (e) => { e.preventDefault(); imgDropzone.classList.add('drag-over'); });
        imgDropzone.addEventListener('dragleave', () => imgDropzone.classList.remove('drag-over'));
        imgDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            imgDropzone.classList.remove('drag-over');
            const files = e.dataTransfer.files;
            if (files.length) handleImageFile(files[0]);
        });
        imgFileInput.addEventListener('change', () => {
            if (imgFileInput.files.length) handleImageFile(imgFileInput.files[0]);
        });

        // Mode selection
        document.querySelectorAll('[data-imgmode]').forEach((card) => {
            card.addEventListener('click', () => {
                if (imgState.processing) return;
                document.querySelectorAll('[data-imgmode]').forEach((c) => c.classList.remove('selected'));
                card.classList.add('selected');
                imgState.selectedMode = card.dataset.imgmode;
                updateModeUI();
                updateImgOutputRes();
            });
        });

        // Scale buttons
        document.querySelectorAll('[data-imgscale]').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (imgState.processing) return;
                document.querySelectorAll('[data-imgscale]').forEach((b) => b.classList.remove('selected'));
                btn.classList.add('selected');
                imgState.selectedScale = parseInt(btn.dataset.imgscale);
                updateImgOutputRes();
            });
        });

        // Upscale button
        imgUpscaleBtn.addEventListener('click', startImageUpscale);

        // Download
        imgDownloadBtn.addEventListener('click', downloadImageResult);

        // New image
        imgNewBtn.addEventListener('click', resetImageApp);
    }

    async function handleImageFile(file) {
        if (!file.type.startsWith('image/') && !file.name.match(/\.(png|jpe?g|webp|bmp|tiff?)$/i)) {
            showToast('Please select an image file.');
            return;
        }
        if (file.size > 50 * 1024 * 1024) {
            showToast('File too large. Maximum 50MB.');
            return;
        }

        imgState.file = file;

        // Show preview immediately
        const url = URL.createObjectURL(file);
        imgOriginal.src = url;

        // Get dimensions from the loaded image
        imgOriginal.onload = () => {
            imgState.imageInfo = {
                width: imgOriginal.naturalWidth,
                height: imgOriginal.naturalHeight,
                format: file.type.split('/')[1]?.toUpperCase() || file.name.split('.').pop().toUpperCase(),
                size: file.size,
            };
            populateImgInfo(imgState.imageInfo);
            updateImgOutputRes();
        };

        imgUploadSection.classList.add('hidden');
        imgAppSection.classList.remove('hidden');
        imgUpscaleBtn.disabled = false;
    }

    function populateImgInfo(info) {
        $('#img-info-resolution').textContent = `${info.width} x ${info.height}`;
        $('#img-info-format').textContent = info.format;
        $('#img-info-size').textContent = formatSize(info.size);
        $('#img-original-res').textContent = `${info.width}x${info.height}`;
    }

    function updateModeUI() {
        const scaleGroup = $('#img-scale-buttons').closest('.option-group');
        const outputLabel = $('#img-output-resolution').closest('.option-group');
        if (imgState.selectedMode === 'enhance') {
            // Hide scale buttons for enhance mode — same size output
            scaleGroup.style.opacity = '0.3';
            scaleGroup.style.pointerEvents = 'none';
            if (outputLabel) {
                outputLabel.querySelector('label').textContent = 'Output Resolution';
            }
            imgUpscaleBtn.innerHTML = enhanceIconSVG() + ' Enhance Image';
        } else {
            scaleGroup.style.opacity = '1';
            scaleGroup.style.pointerEvents = 'auto';
            if (outputLabel) {
                outputLabel.querySelector('label').textContent = 'Output Resolution';
            }
            imgUpscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        }
    }

    function updateImgOutputRes() {
        if (!imgState.imageInfo) return;
        if (imgState.selectedMode === 'enhance') {
            // Same resolution for enhance
            $('#img-output-resolution').textContent = `${imgState.imageInfo.width} x ${imgState.imageInfo.height} (enhanced)`;
            return;
        }
        let w = imgState.imageInfo.width * imgState.selectedScale;
        let h = imgState.imageInfo.height * imgState.selectedScale;
        // Cap at 8K for images
        if (w > 7680) { const r = 7680 / w; w = 7680; h = Math.round(h * r); }
        w = w - (w % 2);
        h = h - (h % 2);
        $('#img-output-resolution').textContent = `${w} x ${h}`;
    }

    async function startImageUpscale() {
        if (imgState.processing || !imgState.file) return;
        imgState.processing = true;

        imgUpscaleBtn.disabled = true;
        imgProgressSection.classList.remove('hidden');
        imgDownloadSection.classList.add('hidden');
        imgNewRow.classList.add('hidden');

        const startTime = Date.now();

        if (imgState.selectedMode === 'enhance') {
            await runEnhance(startTime);
        } else if (imgState.selectedMode === 'quick') {
            await runQuickUpscale(startTime);
        } else {
            await runQualityUpscale(startTime);
        }
    }

    // --- Quick Mode (browser WebGPU / WASM via Real-ESRGAN) ---
    async function runQuickUpscale(startTime) {
        try {
            updateImgProgress({ progress: 0, message: 'Loading AI model...' }, startTime);

            const upscaler = new ProUpscaler();
            await upscaler.init('/static/models/realesr-general-x4v3.onnx');
            const backend = upscaler.getBackend();
            updateImgProgress({ progress: 10, message: `Model loaded (${backend.toUpperCase()})` }, startTime);

            // Draw image to canvas to get ImageData
            const canvas = $('#img-input-canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = imgOriginal.naturalWidth;
            canvas.height = imgOriginal.naturalHeight;
            ctx.drawImage(imgOriginal, 0, 0);
            const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);

            updateImgProgress({ progress: 15, message: `AI upscaling image [${backend.toUpperCase()}]...` }, startTime);

            // Track tile progress
            upscaler.onTileProgress = (done, total) => {
                const pct = Math.min(90, 15 + Math.round((done / total) * 75));
                updateImgProgress({ progress: pct, message: `AI upscaling tile ${done}/${total} [${backend.toUpperCase()}]` }, startTime);
            };

            const upscaled = await upscaler.processFrame(imageData);

            updateImgProgress({ progress: 92, message: 'Rendering result...' }, startTime);

            // If user chose 2x but model is 4x, we downscale the output
            let finalData = upscaled;
            if (imgState.selectedScale === 2) {
                const tempCanvas = document.createElement('canvas');
                tempCanvas.width = upscaled.width;
                tempCanvas.height = upscaled.height;
                const tempCtx = tempCanvas.getContext('2d');
                tempCtx.putImageData(upscaled, 0, 0);

                const halfCanvas = document.createElement('canvas');
                halfCanvas.width = Math.round(upscaled.width / 2);
                halfCanvas.height = Math.round(upscaled.height / 2);
                const halfCtx = halfCanvas.getContext('2d');
                halfCtx.drawImage(tempCanvas, 0, 0, halfCanvas.width, halfCanvas.height);

                finalData = halfCtx.getImageData(0, 0, halfCanvas.width, halfCanvas.height);
            }

            // Draw to output canvas
            const outCanvas = $('#img-output-canvas');
            outCanvas.width = finalData.width;
            outCanvas.height = finalData.height;
            const outCtx = outCanvas.getContext('2d');
            outCtx.putImageData(finalData, 0, 0);

            // Convert to blob for display and download
            const blob = await new Promise((resolve) => outCanvas.toBlob(resolve, 'image/png'));
            const resultUrl = URL.createObjectURL(blob);

            imgUpscaled.src = resultUrl;
            imgUpscaled.classList.remove('hidden');
            imgOutputPlaceholder.classList.add('hidden');
            $('#img-upscaled-res').textContent = `${finalData.width}x${finalData.height}`;

            // Store blob URL for download
            imgState.resultUrl = resultUrl;
            imgState.resultBlob = blob;

            onImageUpscaleComplete(finalData.width, finalData.height, backend);
            upscaler.dispose();
        } catch (err) {
            onImageUpscaleError('Quick upscale failed: ' + err.message);
        }
    }

    // --- Enhance Mode (AI 4x upscale → downscale back to original size) ---
    async function runEnhance(startTime) {
        try {
            updateImgProgress({ progress: 0, message: 'Loading AI model...' }, startTime);

            const upscaler = new ProUpscaler();
            await upscaler.init('/static/models/realesr-general-x4v3.onnx');
            const backend = upscaler.getBackend();
            updateImgProgress({ progress: 10, message: `Model loaded (${backend.toUpperCase()})` }, startTime);

            // Draw original image to canvas
            const canvas = $('#img-input-canvas');
            const ctx = canvas.getContext('2d');
            const origW = imgOriginal.naturalWidth;
            const origH = imgOriginal.naturalHeight;
            canvas.width = origW;
            canvas.height = origH;
            ctx.drawImage(imgOriginal, 0, 0);
            const imageData = ctx.getImageData(0, 0, origW, origH);

            updateImgProgress({ progress: 15, message: `Enhancing with AI [${backend.toUpperCase()}]...` }, startTime);

            // Track tile progress
            upscaler.onTileProgress = (done, total) => {
                const pct = Math.min(80, 15 + Math.round((done / total) * 65));
                updateImgProgress({ progress: pct, message: `AI enhancing tile ${done}/${total} [${backend.toUpperCase()}]` }, startTime);
            };

            // AI upscale to 4x
            const upscaled = await upscaler.processFrame(imageData);

            updateImgProgress({ progress: 85, message: 'Downscaling to original resolution...' }, startTime);

            // Render 4x result to a temp canvas
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = upscaled.width;
            tempCanvas.height = upscaled.height;
            const tempCtx = tempCanvas.getContext('2d');
            tempCtx.putImageData(upscaled, 0, 0);

            // Downscale back to original size using high-quality canvas resampling
            const outCanvas = $('#img-output-canvas');
            outCanvas.width = origW;
            outCanvas.height = origH;
            const outCtx = outCanvas.getContext('2d');
            outCtx.imageSmoothingEnabled = true;
            outCtx.imageSmoothingQuality = 'high';
            outCtx.drawImage(tempCanvas, 0, 0, origW, origH);

            updateImgProgress({ progress: 95, message: 'Rendering result...' }, startTime);

            // Convert to blob
            const blob = await new Promise((resolve) => outCanvas.toBlob(resolve, 'image/png'));
            const resultUrl = URL.createObjectURL(blob);

            imgUpscaled.src = resultUrl;
            imgUpscaled.classList.remove('hidden');
            imgOutputPlaceholder.classList.add('hidden');
            $('#img-upscaled-res').textContent = `${origW}x${origH}`;

            imgState.resultUrl = resultUrl;
            imgState.resultBlob = blob;

            onImageUpscaleComplete(origW, origH, `${backend.toUpperCase()} Enhanced`);
            upscaler.dispose();
        } catch (err) {
            onImageUpscaleError('Enhance failed: ' + err.message);
        }
    }

    // --- Quality Mode (server-side FFmpeg) ---
    async function runQualityUpscale(startTime) {
        try {
            updateImgProgress({ progress: 0, message: 'Uploading image...' }, startTime);

            const formData = new FormData();
            formData.append('image', imgState.file);
            formData.append('scale', imgState.selectedScale);

            const res = await fetch('/api/image/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Upload failed');

            imgState.taskId = data.task_id;
            updateImgProgress({ progress: 20, message: 'Upscaling on server...' }, startTime);

            // Start upscale
            const upRes = await fetch('/api/image/upscale', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: data.task_id, scale: imgState.selectedScale }),
            });
            if (!upRes.ok) throw new Error('Failed to start upscale');

            // Poll for completion
            const pollInterval = setInterval(async () => {
                try {
                    const statusRes = await fetch(`/api/image/status/${data.task_id}`);
                    const statusData = await statusRes.json();

                    updateImgProgress({ progress: statusData.progress || 50, message: statusData.message || 'Processing...' }, startTime);

                    if (statusData.status === 'completed') {
                        clearInterval(pollInterval);

                        // Show result
                        const imgUrl = `/api/image/download/${data.task_id}`;
                        imgUpscaled.src = imgUrl;
                        imgUpscaled.classList.remove('hidden');
                        imgOutputPlaceholder.classList.add('hidden');

                        imgState.resultUrl = imgUrl;

                        const outW = statusData.output_width || imgState.imageInfo.width * imgState.selectedScale;
                        const outH = statusData.output_height || imgState.imageInfo.height * imgState.selectedScale;
                        $('#img-upscaled-res').textContent = `${outW}x${outH}`;

                        onImageUpscaleComplete(outW, outH, 'FFmpeg');
                    } else if (statusData.status === 'error') {
                        clearInterval(pollInterval);
                        onImageUpscaleError(statusData.message || 'Server error');
                    }
                } catch {
                    clearInterval(pollInterval);
                    onImageUpscaleError('Connection lost');
                }
            }, 500);
        } catch (err) {
            onImageUpscaleError('Quality upscale failed: ' + err.message);
        }
    }

    function updateImgProgress(data, startTime) {
        const pct = data.progress || 0;
        imgProgressFill.style.width = pct + '%';
        imgProgressPercent.textContent = pct + '%';
        imgProgressMessage.textContent = data.message || 'Processing...';

        if (pct > 10 && startTime) {
            const elapsed = (Date.now() - startTime) / 1000;
            const estimated = (elapsed / pct) * (100 - pct);
            imgProgressEta.textContent = 'ETA: ' + formatDuration(estimated);
        }
    }

    function onImageUpscaleComplete(outW, outH, method) {
        imgState.processing = false;
        imgProgressSection.classList.add('hidden');
        imgDownloadSection.classList.remove('hidden');
        imgNewRow.classList.remove('hidden');
        imgUpscaleBtn.disabled = false;
        if (imgState.selectedMode === 'enhance') {
            imgUpscaleBtn.innerHTML = enhanceIconSVG() + ' Enhance Image';
            imgDownloadDetails.textContent = `${method} — ${outW} x ${outH}`;
        } else {
            imgUpscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
            imgDownloadDetails.textContent = `${method} upscaled to ${outW} x ${outH}`;
        }
    }

    function onImageUpscaleError(msg) {
        imgState.processing = false;
        imgProgressSection.classList.add('hidden');
        imgUpscaleBtn.disabled = false;
        if (imgState.selectedMode === 'enhance') {
            imgUpscaleBtn.innerHTML = enhanceIconSVG() + ' Enhance Image';
        } else {
            imgUpscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        }
        showToast('Error: ' + msg);
    }

    function downloadImageResult() {
        if (imgState.resultBlob) {
            // Quick mode — blob download
            const a = document.createElement('a');
            a.href = imgState.resultUrl;
            const stem = imgState.file ? imgState.file.name.replace(/\.[^.]+$/, '') : 'image';
            a.download = `${stem}_upscaled.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } else if (imgState.taskId) {
            // Quality mode — server download
            window.open(`/api/image/download/${imgState.taskId}`, '_blank');
        }
    }

    function resetImageApp() {
        imgState.taskId = null;
        imgState.imageInfo = null;
        imgState.processing = false;
        imgState.file = null;
        imgState.resultUrl = null;
        imgState.resultBlob = null;

        imgUploadSection.classList.remove('hidden');
        imgAppSection.classList.add('hidden');
        imgProgressSection.classList.add('hidden');
        imgDownloadSection.classList.add('hidden');
        imgNewRow.classList.add('hidden');

        imgOriginal.src = '';
        imgUpscaled.src = '';
        imgUpscaled.classList.add('hidden');
        imgOutputPlaceholder.classList.remove('hidden');
        imgFileInput.value = '';
    }

    // --- Utils (shared signatures with app.js) ---
    function formatDuration(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    }

    function formatSize(bytes) {
        if (bytes > 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
        if (bytes > 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        return (bytes / 1024).toFixed(0) + ' KB';
    }

    function showToast(msg) {
        const existing = document.querySelector('.toast');
        if (existing) existing.remove();
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = msg;
        document.body.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }

    function upscaleIconSVG() {
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>';
    }

    function enhanceIconSVG() {
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>';
    }
})();
