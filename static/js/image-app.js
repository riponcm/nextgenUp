/**
 * Upscale4K — Image upscaling controller.
 * Single-image modes: Quick (browser AI), Quality (server FFmpeg),
 * Enhance (browser AI, same size), Ultra (server AI).
 * Batch mode: multiple images through any mode, zip download.
 */
(function () {
    'use strict';

    const MODEL_PATH = '/static/models/realesr-general-x4v3.onnx';

    // --- State ---
    const imgState = {
        taskId: null,
        imageInfo: null,
        selectedMode: 'quick',
        selectedScale: 4,
        processing: false,
        file: null,
        originalUrl: null,
        resultUrl: null,
        resultBlob: null,
        cancelRequested: false,
        activeUpscaler: null,
    };

    const batch = {
        items: [],       // { file, url, status, blob, el }
        running: false,
        scale: 4,
        cancelRequested: false,
        activeUpscaler: null,
        currentTaskId: null,
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
    const imgProgressEta = $('#img-progress-eta');
    const imgDownloadSection = $('#img-download-section');
    const imgDownloadBtn = $('#img-download-btn');
    const imgDownloadDetails = $('#img-download-details');
    const imgNewRow = $('#img-new-row');
    const imgNewBtn = $('#img-new-btn');
    // Compare slider
    const compareSection = $('#img-compare-section');
    const compareBox = $('#compare-box');
    const compareBefore = $('#compare-before');
    const compareAfter = $('#compare-after');
    const compareBeforeWrap = $('#compare-before-wrap');
    const compareHandle = $('#compare-handle');
    // Batch
    const batchSection = $('#img-batch-section');
    const batchTitle = $('#batch-title');
    const batchSub = $('#batch-sub');
    const batchGrid = $('#batch-grid');
    const batchStartBtn = $('#batch-start-btn');
    const batchZipBtn = $('#batch-zip-btn');
    const batchResetBtn = $('#batch-reset-btn');
    const batchModeSel = $('#batch-mode');
    const batchProgressLabel = $('#batch-progress-label');

    // --- Init ---
    bindImageEvents();
    initCompareSlider();

    function bindImageEvents() {
        imgDropzone.addEventListener('click', () => imgFileInput.click());
        imgDropzone.addEventListener('dragover', (e) => { e.preventDefault(); imgDropzone.classList.add('drag-over'); });
        imgDropzone.addEventListener('dragleave', () => imgDropzone.classList.remove('drag-over'));
        imgDropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            imgDropzone.classList.remove('drag-over');
            if (e.dataTransfer.files.length) handleImageFiles(e.dataTransfer.files);
        });
        imgFileInput.addEventListener('change', () => {
            if (imgFileInput.files.length) handleImageFiles(imgFileInput.files);
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

        // Scale buttons (single mode)
        document.querySelectorAll('[data-imgscale]').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (imgState.processing) return;
                document.querySelectorAll('[data-imgscale]').forEach((b) => b.classList.remove('selected'));
                btn.classList.add('selected');
                imgState.selectedScale = parseInt(btn.dataset.imgscale);
                updateImgOutputRes();
            });
        });

        // Scale buttons (batch)
        document.querySelectorAll('[data-batchscale]').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (batch.running) return;
                document.querySelectorAll('[data-batchscale]').forEach((b) => b.classList.remove('selected'));
                btn.classList.add('selected');
                batch.scale = parseInt(btn.dataset.batchscale);
            });
        });

        imgUpscaleBtn.addEventListener('click', startImageUpscale);
        imgDownloadBtn.addEventListener('click', downloadImageResult);
        imgNewBtn.addEventListener('click', resetImageApp);
        $('#img-cancel-btn').addEventListener('click', cancelImageJob);

        batchStartBtn.addEventListener('click', startBatch);
        batchZipBtn.addEventListener('click', downloadBatchZip);
        batchResetBtn.addEventListener('click', resetBatch);
    }

    function isValidImage(file) {
        return file.type.startsWith('image/') || file.name.match(/\.(png|jpe?g|webp|bmp|tiff?)$/i);
    }

    function handleImageFiles(fileList) {
        const files = Array.from(fileList).filter((f) => {
            if (!isValidImage(f)) return false;
            if (f.size > 50 * 1024 * 1024) return false;
            return true;
        });
        if (!files.length) {
            showToast('Please select image files under 50MB.');
            return;
        }
        if (files.length === 1) {
            handleSingleImage(files[0]);
        } else {
            enterBatch(files);
        }
    }

    // ============================== SINGLE IMAGE ==============================

    function handleSingleImage(file) {
        imgState.file = file;

        if (imgState.originalUrl) URL.revokeObjectURL(imgState.originalUrl);
        imgState.originalUrl = URL.createObjectURL(file);
        imgOriginal.src = imgState.originalUrl;

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
        imgNewRow.classList.remove('hidden');
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
        if (imgState.selectedMode === 'enhance') {
            scaleGroup.style.opacity = '0.3';
            scaleGroup.style.pointerEvents = 'none';
            imgUpscaleBtn.innerHTML = enhanceIconSVG() + ' Enhance Image';
        } else {
            scaleGroup.style.opacity = '1';
            scaleGroup.style.pointerEvents = 'auto';
            imgUpscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        }
        // Face restoration runs on the server — only offered in Ultra mode
        const faceGroup = $('#face-restore-group');
        if (faceGroup) faceGroup.classList.toggle('hidden', imgState.selectedMode !== 'ultra');
    }

    function updateImgOutputRes() {
        if (!imgState.imageInfo) return;
        if (imgState.selectedMode === 'enhance') {
            $('#img-output-resolution').textContent = `${imgState.imageInfo.width} x ${imgState.imageInfo.height} (enhanced)`;
            return;
        }
        let w = imgState.imageInfo.width * imgState.selectedScale;
        let h = imgState.imageInfo.height * imgState.selectedScale;
        const maxDim = 7680;
        if (w > maxDim || h > maxDim) {
            if (w >= h) { const r = maxDim / w; w = maxDim; h = Math.round(h * r); }
            else { const r = maxDim / h; h = maxDim; w = Math.round(w * r); }
        }
        w = w - (w % 2);
        h = h - (h % 2);
        $('#img-output-resolution').textContent = `${w} x ${h}`;
    }

    function cancelImageJob() {
        if (!imgState.processing) return;
        imgState.cancelRequested = true;
        if (imgState.activeUpscaler) imgState.activeUpscaler.cancel();
        if (imgState.taskId) {
            fetch(`/api/image/cancel/${imgState.taskId}`, { method: 'POST' }).catch(() => {});
        }
    }

    function onImageCancelled() {
        imgState.processing = false;
        imgState.activeUpscaler = null;
        imgProgressSection.classList.add('hidden');
        imgNewRow.classList.remove('hidden');
        imgUpscaleBtn.disabled = false;
        updateModeUI();
        showToast('Cancelled — pick any mode and start again.');
    }

    function isCancelError(err) {
        return imgState.cancelRequested || (err && err.message === 'cancelled');
    }

    async function startImageUpscale() {
        if (imgState.processing || !imgState.file) return;
        imgState.processing = true;
        imgState.cancelRequested = false;
        imgState.taskId = null;

        imgUpscaleBtn.disabled = true;
        imgProgressSection.classList.remove('hidden');
        imgDownloadSection.classList.add('hidden');
        compareSection.classList.add('hidden');
        imgNewRow.classList.add('hidden');

        const startTime = Date.now();

        if (imgState.selectedMode === 'enhance') {
            await runEnhance(startTime);
        } else if (imgState.selectedMode === 'quick') {
            await runQuickUpscale(startTime);
        } else if (imgState.selectedMode === 'ultra') {
            await runServerUpscale(startTime, 'ai');
        } else {
            await runServerUpscale(startTime, 'ffmpeg');
        }
    }

    // --- Quick Mode (browser WebGPU / WASM via Real-ESRGAN) ---
    async function runQuickUpscale(startTime) {
        try {
            updateImgProgress({ progress: 0, message: 'Loading AI model...' }, startTime);

            const upscaler = new ProUpscaler();
            imgState.activeUpscaler = upscaler;
            await upscaler.init(MODEL_PATH);
            const backend = upscaler.getBackend();
            updateImgProgress({ progress: 10, message: `Model loaded (${backend.toUpperCase()})` }, startTime);

            upscaler.onTileProgress = (done, total) => {
                const pct = Math.min(90, 15 + Math.round((done / total) * 75));
                updateImgProgress({ progress: pct, message: `AI upscaling tile ${done}/${total} [${backend.toUpperCase()}]` }, startTime);
            };

            const finalData = await localUpscaleImageData(imgOriginal, upscaler, 'quick', imgState.selectedScale);

            updateImgProgress({ progress: 95, message: 'Rendering result...' }, startTime);
            const blob = await imageDataToBlob(finalData);
            const resultUrl = URL.createObjectURL(blob);

            showSingleResult(resultUrl, blob, finalData.width, finalData.height, backend.toUpperCase());
            imgState.activeUpscaler = null;
            upscaler.dispose();
        } catch (err) {
            if (isCancelError(err)) { onImageCancelled(); return; }
            onImageUpscaleError('Quick upscale failed: ' + err.message);
        }
    }

    // --- Enhance Mode (AI 4x upscale → downscale back to original size) ---
    async function runEnhance(startTime) {
        try {
            updateImgProgress({ progress: 0, message: 'Loading AI model...' }, startTime);

            const upscaler = new ProUpscaler();
            imgState.activeUpscaler = upscaler;
            await upscaler.init(MODEL_PATH);
            const backend = upscaler.getBackend();
            updateImgProgress({ progress: 10, message: `Model loaded (${backend.toUpperCase()})` }, startTime);

            upscaler.onTileProgress = (done, total) => {
                const pct = Math.min(80, 15 + Math.round((done / total) * 65));
                updateImgProgress({ progress: pct, message: `AI enhancing tile ${done}/${total} [${backend.toUpperCase()}]` }, startTime);
            };

            const finalData = await localUpscaleImageData(imgOriginal, upscaler, 'enhance', 4);

            updateImgProgress({ progress: 95, message: 'Rendering result...' }, startTime);
            const blob = await imageDataToBlob(finalData);
            const resultUrl = URL.createObjectURL(blob);

            showSingleResult(resultUrl, blob, finalData.width, finalData.height, `${backend.toUpperCase()} Enhanced`);
            imgState.activeUpscaler = null;
            upscaler.dispose();
        } catch (err) {
            if (isCancelError(err)) { onImageCancelled(); return; }
            onImageUpscaleError('Enhance failed: ' + err.message);
        }
    }

    // --- Server Modes: Quality (FFmpeg) and Ultra (server AI) ---
    async function runServerUpscale(startTime, serverMode) {
        try {
            updateImgProgress({ progress: 0, message: 'Uploading image...' }, startTime);

            const formData = new FormData();
            formData.append('image', imgState.file);

            const res = await fetch('/api/image/upload', { method: 'POST', body: formData });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Upload failed');

            imgState.taskId = data.task_id;
            updateImgProgress({ progress: 15, message: 'Processing on server...' }, startTime);

            const faceCheck = $('#face-restore-check');
            const upRes = await fetch('/api/image/upscale', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: data.task_id,
                    scale: imgState.selectedScale,
                    mode: serverMode,
                    face_restore: !!(serverMode === 'ai' && faceCheck && faceCheck.checked),
                }),
            });
            if (!upRes.ok) {
                const err = await upRes.json().catch(() => ({}));
                throw new Error(err.error || 'Failed to start upscale');
            }

            const statusData = await pollImageStatus(data.task_id,
                (s) => updateImgProgress(s, startTime),
                () => imgState.cancelRequested);

            const imgUrl = `/api/image/download/${data.task_id}`;
            const outW = statusData.output_width || imgState.imageInfo.width * imgState.selectedScale;
            const outH = statusData.output_height || imgState.imageInfo.height * imgState.selectedScale;

            showSingleResult(imgUrl, null, outW, outH, serverMode === 'ai' ? 'Server AI' : 'FFmpeg');
        } catch (err) {
            if (isCancelError(err)) { onImageCancelled(); return; }
            onImageUpscaleError('Upscale failed: ' + err.message);
        }
    }

    function pollImageStatus(taskId, onProgress, isCancelled) {
        return new Promise((resolve, reject) => {
            const interval = setInterval(async () => {
                if (isCancelled && isCancelled()) {
                    clearInterval(interval);
                    reject(new Error('cancelled'));
                    return;
                }
                try {
                    const res = await fetch(`/api/image/status/${taskId}`);
                    const s = await res.json();
                    if (onProgress) onProgress({ progress: s.progress || 50, message: s.message || 'Processing...' });
                    if (s.status === 'completed') { clearInterval(interval); resolve(s); }
                    else if (s.status === 'cancelled') { clearInterval(interval); reject(new Error('cancelled')); }
                    else if (s.status === 'error') { clearInterval(interval); reject(new Error(s.message || 'Server error')); }
                } catch (e) {
                    clearInterval(interval);
                    reject(new Error('Connection lost'));
                }
            }, 500);
        });
    }

    function showSingleResult(resultUrl, blob, outW, outH, method) {
        imgUpscaled.src = resultUrl;
        imgUpscaled.classList.remove('hidden');
        imgOutputPlaceholder.classList.add('hidden');
        $('#img-upscaled-res').textContent = `${outW}x${outH}`;

        imgState.resultUrl = resultUrl;
        imgState.resultBlob = blob;

        imgState.processing = false;
        imgProgressSection.classList.add('hidden');
        imgDownloadSection.classList.remove('hidden');
        imgNewRow.classList.remove('hidden');
        imgUpscaleBtn.disabled = false;
        updateModeUI();

        const label = imgState.selectedMode === 'enhance'
            ? `${method} — ${outW} x ${outH}`
            : `${method} upscaled to ${outW} x ${outH}`;
        imgDownloadDetails.textContent = label;

        showCompare(imgState.originalUrl, resultUrl);
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

    function onImageUpscaleError(msg) {
        imgState.processing = false;
        imgProgressSection.classList.add('hidden');
        imgNewRow.classList.remove('hidden');
        imgUpscaleBtn.disabled = false;
        updateModeUI();
        showToast('Error: ' + msg);
    }

    function downloadImageResult() {
        if (imgState.resultBlob) {
            const a = document.createElement('a');
            a.href = imgState.resultUrl;
            const stem = imgState.file ? imgState.file.name.replace(/\.[^.]+$/, '') : 'image';
            a.download = `${stem}_upscaled.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } else if (imgState.taskId) {
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
        compareSection.classList.add('hidden');
        imgNewRow.classList.add('hidden');

        imgOriginal.src = '';
        imgUpscaled.src = '';
        imgUpscaled.classList.add('hidden');
        imgOutputPlaceholder.classList.remove('hidden');
        imgFileInput.value = '';
    }

    // ============================== LOCAL AI HELPERS ==============================

    /** Run browser AI on an image element or bitmap. mode: 'quick' | 'enhance'. */
    async function localUpscaleImageData(source, upscaler, mode, scale) {
        const srcW = source.naturalWidth || source.width;
        const srcH = source.naturalHeight || source.height;

        const canvas = $('#img-input-canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = srcW;
        canvas.height = srcH;
        ctx.drawImage(source, 0, 0);
        const imageData = ctx.getImageData(0, 0, srcW, srcH);

        const upscaled = await upscaler.processFrame(imageData);

        let targetW, targetH;
        if (mode === 'enhance') {
            targetW = srcW; targetH = srcH;
        } else if (scale === 2) {
            targetW = srcW * 2; targetH = srcH * 2;
        } else {
            return upscaled;
        }

        // High-quality downscale via canvas
        const tmp = document.createElement('canvas');
        tmp.width = upscaled.width;
        tmp.height = upscaled.height;
        tmp.getContext('2d').putImageData(upscaled, 0, 0);

        const out = document.createElement('canvas');
        out.width = targetW;
        out.height = targetH;
        const outCtx = out.getContext('2d');
        outCtx.imageSmoothingEnabled = true;
        outCtx.imageSmoothingQuality = 'high';
        outCtx.drawImage(tmp, 0, 0, targetW, targetH);
        return outCtx.getImageData(0, 0, targetW, targetH);
    }

    function imageDataToBlob(imageData) {
        const canvas = $('#img-output-canvas');
        canvas.width = imageData.width;
        canvas.height = imageData.height;
        canvas.getContext('2d').putImageData(imageData, 0, 0);
        return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    }

    // ============================== COMPARE SLIDER ==============================

    function initCompareSlider() {
        let dragging = false;

        const setPos = (clientX) => {
            const rect = compareBox.getBoundingClientRect();
            let pct = ((clientX - rect.left) / rect.width) * 100;
            pct = Math.max(2, Math.min(98, pct));
            compareBeforeWrap.style.width = pct + '%';
            compareHandle.style.left = pct + '%';
        };

        compareBox.addEventListener('pointerdown', (e) => {
            dragging = true;
            compareBox.setPointerCapture(e.pointerId);
            setPos(e.clientX);
        });
        compareBox.addEventListener('pointermove', (e) => { if (dragging) setPos(e.clientX); });
        compareBox.addEventListener('pointerup', () => { dragging = false; });
        compareBox.addEventListener('pointercancel', () => { dragging = false; });

        window.addEventListener('resize', syncCompareSizes);
    }

    function syncCompareSizes() {
        if (compareSection.classList.contains('hidden')) return;
        // The "before" image must render at exactly the same size as the
        // "after" image so the two halves line up.
        compareBefore.style.width = compareAfter.clientWidth + 'px';
        compareBefore.style.height = compareAfter.clientHeight + 'px';
    }

    function showCompare(beforeUrl, afterUrl) {
        if (!beforeUrl || !afterUrl) return;
        compareBefore.src = beforeUrl;
        compareAfter.src = afterUrl;
        compareSection.classList.remove('hidden');
        compareBeforeWrap.style.width = '50%';
        compareHandle.style.left = '50%';
        compareAfter.onload = syncCompareSizes;
        requestAnimationFrame(syncCompareSizes);
    }

    // ============================== BATCH ==============================

    function enterBatch(files) {
        resetBatchState();
        batch.items = files.map((file) => ({
            file,
            url: URL.createObjectURL(file),
            status: 'pending',
            blob: null,
            el: null,
        }));

        imgUploadSection.classList.add('hidden');
        imgAppSection.classList.add('hidden');
        batchSection.classList.remove('hidden');

        batchTitle.textContent = `Batch: ${batch.items.length} images`;
        batchSub.textContent = 'Pick a mode and scale, then start';
        batchProgressLabel.textContent = '—';
        batchZipBtn.classList.add('hidden');
        batchStartBtn.disabled = false;
        batchResetBtn.textContent = 'Cancel';

        batchGrid.innerHTML = '';
        batch.items.forEach((item) => {
            const div = document.createElement('div');
            div.className = 'batch-item';
            const img = document.createElement('img');
            img.src = item.url;
            const info = document.createElement('div');
            info.className = 'batch-item-info';
            const name = document.createElement('span');
            name.className = 'batch-item-name';
            name.textContent = item.file.name;
            const status = document.createElement('span');
            status.className = 'batch-status pending';
            status.textContent = 'queued';
            info.appendChild(name);
            info.appendChild(status);
            div.appendChild(img);
            div.appendChild(info);
            batchGrid.appendChild(div);
            item.el = { status, info, img };
        });
    }

    function setItemStatus(item, cls, text) {
        item.el.status.className = 'batch-status ' + cls;
        item.el.status.textContent = text;
    }

    async function startBatch() {
        if (batch.running || !batch.items.length) return;
        batch.running = true;
        batch.cancelRequested = false;
        batchStartBtn.disabled = true;
        batchZipBtn.classList.add('hidden');
        batchResetBtn.textContent = 'Cancel';

        const mode = batchModeSel.value;
        const scale = batch.scale;
        const isLocal = mode === 'quick' || mode === 'enhance';

        let upscaler = null;
        try {
            if (isLocal) {
                batchSub.textContent = 'Loading AI model...';
                upscaler = new ProUpscaler();
                batch.activeUpscaler = upscaler;
                await upscaler.init(MODEL_PATH);
                batchSub.textContent = `Running in your browser (${upscaler.getBackend().toUpperCase()})`;
            } else {
                batchSub.textContent = mode === 'ultra' ? 'Running on server (AI)' : 'Running on server (FFmpeg)';
            }

            let done = 0;
            for (const item of batch.items) {
                if (batch.cancelRequested) break;
                batchProgressLabel.textContent = `${done + 1} / ${batch.items.length}`;
                setItemStatus(item, 'working', '0%');
                try {
                    if (isLocal) {
                        upscaler.onTileProgress = (d, t) => setItemStatus(item, 'working', Math.round((d / t) * 100) + '%');
                        const bitmap = await createImageBitmap(item.file);
                        const data = await localUpscaleImageData(bitmap, upscaler, mode, scale);
                        bitmap.close();
                        item.blob = await imageDataToBlob(data);
                    } else {
                        item.blob = await serverProcessToBlob(item.file, mode === 'ultra' ? 'ai' : 'ffmpeg', scale,
                            (pct) => setItemStatus(item, 'working', pct + '%'));
                    }
                    setItemStatus(item, 'done', 'done');
                    addItemDownloadLink(item);
                } catch (e) {
                    if (batch.cancelRequested || (e && e.message === 'cancelled')) {
                        setItemStatus(item, 'failed', 'cancelled');
                        break;
                    }
                    console.error('batch item failed', e);
                    setItemStatus(item, 'failed', 'failed');
                }
                done++;
            }

            if (batch.cancelRequested) {
                batchProgressLabel.textContent = 'Cancelled';
                batchSub.textContent = `Cancelled — ${batch.items.filter(i => i.blob).length} finished before stopping`;
            } else {
                batchProgressLabel.textContent = 'Complete';
                batchSub.textContent = `${batch.items.filter(i => i.blob).length} of ${batch.items.length} succeeded`;
            }
            if (batch.items.some(i => i.blob)) batchZipBtn.classList.remove('hidden');
        } catch (err) {
            showToast('Batch failed: ' + err.message);
        } finally {
            if (upscaler && upscaler.worker) upscaler.dispose();
            batch.activeUpscaler = null;
            batch.currentTaskId = null;
            batch.cancelRequested = false;
            batch.running = false;
            batchStartBtn.disabled = false;
            batchResetBtn.textContent = 'Close';
        }
    }

    async function serverProcessToBlob(file, serverMode, scale, onPct) {
        const formData = new FormData();
        formData.append('image', file);
        const res = await fetch('/api/image/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Upload failed');
        batch.currentTaskId = data.task_id;

        const upRes = await fetch('/api/image/upscale', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: data.task_id, scale, mode: serverMode }),
        });
        if (!upRes.ok) {
            const err = await upRes.json().catch(() => ({}));
            throw new Error(err.error || 'Failed to start');
        }

        await pollImageStatus(data.task_id,
            (s) => onPct && onPct(s.progress || 0),
            () => batch.cancelRequested);
        batch.currentTaskId = null;

        const dl = await fetch(`/api/image/download/${data.task_id}`);
        if (!dl.ok) throw new Error('Download failed');
        return await dl.blob();
    }

    function addItemDownloadLink(item) {
        const a = document.createElement('a');
        a.textContent = 'save';
        a.href = URL.createObjectURL(item.blob);
        a.download = item.file.name.replace(/\.[^.]+$/, '') + '_upscaled.png';
        item.el.info.appendChild(a);
        // Show the result as the thumbnail
        item.el.img.src = a.href;
    }

    async function downloadBatchZip() {
        if (typeof JSZip === 'undefined') {
            showToast('JSZip not loaded — check your connection.');
            return;
        }
        const zip = new JSZip();
        for (const item of batch.items) {
            if (item.blob) {
                zip.file(item.file.name.replace(/\.[^.]+$/, '') + '_upscaled.png', item.blob);
            }
        }
        batchZipBtn.disabled = true;
        batchZipBtn.textContent = 'Zipping...';
        try {
            const blob = await zip.generateAsync({ type: 'blob' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'upscaled_images.zip';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(a.href), 10000);
        } finally {
            batchZipBtn.disabled = false;
            batchZipBtn.textContent = 'Download All (.zip)';
        }
    }

    function resetBatchState() {
        batch.items.forEach((i) => { if (i.url) URL.revokeObjectURL(i.url); });
        batch.items = [];
        batch.running = false;
    }

    function resetBatch() {
        if (batch.running) {
            // While running, this button acts as Cancel
            batch.cancelRequested = true;
            if (batch.activeUpscaler) batch.activeUpscaler.cancel();
            if (batch.currentTaskId) {
                fetch(`/api/image/cancel/${batch.currentTaskId}`, { method: 'POST' }).catch(() => {});
            }
            batchSub.textContent = 'Cancelling...';
            return;
        }
        resetBatchState();
        batchGrid.innerHTML = '';
        batchSection.classList.add('hidden');
        imgUploadSection.classList.remove('hidden');
        imgFileInput.value = '';
    }

    // ============================== UTILS ==============================

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
