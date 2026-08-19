/**
 * Upscale4K — Main application controller.
 */
(function () {
    'use strict';

    // --- State ---
    const state = {
        taskId: null,
        videoInfo: null,
        selectedMode: 'basic',
        selectedScale: 4,
        processing: false,
        file: null,
    };

    // --- DOM refs ---
    const $ = (sel) => document.querySelector(sel);
    const dropzone = $('#dropzone');
    const fileInput = $('#file-input');
    const uploadSection = $('#upload-section');
    const appSection = $('#app-section');
    const originalVideo = $('#original-video');
    const upscaledVideo = $('#upscaled-video');
    const outputPlaceholder = $('#output-placeholder');
    const upscaleBtn = $('#upscale-btn');
    const progressSection = $('#progress-section');
    const progressFill = $('#progress-fill');
    const progressMessage = $('#progress-message');
    const progressPercent = $('#progress-percent');
    const progressFrames = $('#progress-frames');
    const progressEta = $('#progress-eta');
    const downloadSection = $('#download-section');
    const downloadBtn = $('#download-btn');
    const downloadDetails = $('#download-details');
    const newVideoRow = $('#new-video-row');
    const newVideoBtn = $('#new-video-btn');
    const gpuBadge = $('#gpu-badge');
    const gpuStatus = $('#gpu-status');
    const proWarning = $('#pro-warning');

    // --- Init ---
    checkWebGPU();
    bindEvents();
    initSidebar();

    async function checkWebGPU() {
        const dot = gpuBadge.querySelector('.badge-dot');
        if (!navigator.gpu) {
            dot.classList.add('unavailable');
            gpuStatus.textContent = 'WebGPU unavailable';
            showProWarning('WebGPU not supported in this browser. Use Chrome 113+.');
            return;
        }
        try {
            const adapter = await navigator.gpu.requestAdapter();
            if (adapter) {
                dot.classList.add('active');
                gpuStatus.textContent = 'WebGPU ready';
            } else {
                dot.classList.add('unavailable');
                gpuStatus.textContent = 'No GPU adapter';
                showProWarning('No WebGPU adapter found.');
            }
        } catch {
            dot.classList.add('unavailable');
            gpuStatus.textContent = 'WebGPU error';
        }
    }

    function showProWarning(msg) {
        proWarning.textContent = msg;
        proWarning.classList.remove('hidden');
    }

    function bindEvents() {
        // Dropzone
        dropzone.addEventListener('click', () => fileInput.click());
        dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
        dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('drag-over');
            const files = e.dataTransfer.files;
            if (files.length) handleFile(files[0]);
        });
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length) handleFile(fileInput.files[0]);
        });

        // Mode selection
        document.querySelectorAll('.mode-card').forEach((card) => {
            card.addEventListener('click', () => {
                if (state.processing || card.classList.contains('disabled')) return;
                document.querySelectorAll('.mode-card').forEach((c) => c.classList.remove('selected'));
                card.classList.add('selected');
                state.selectedMode = card.dataset.mode;
                updateOutputRes();
            });
        });

        // Scale buttons
        document.querySelectorAll('.scale-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                if (state.processing) return;
                document.querySelectorAll('.scale-btn').forEach((b) => b.classList.remove('selected'));
                btn.classList.add('selected');
                state.selectedScale = parseInt(btn.dataset.scale);
                updateOutputRes();
            });
        });

        // Upscale button
        upscaleBtn.addEventListener('click', startUpscale);

        // Download
        downloadBtn.addEventListener('click', downloadResult);

        // New video
        newVideoBtn.addEventListener('click', resetApp);
    }

    async function handleFile(file) {
        if (!file.type.startsWith('video/') && !file.name.match(/\.(mp4|mov|webm|mkv|avi)$/i)) {
            showToast('Please select a video file.');
            return;
        }
        if (file.size > 2 * 1024 * 1024 * 1024) {
            showToast('File too large. Maximum 2GB.');
            return;
        }

        state.file = file;

        // Show preview immediately
        const url = URL.createObjectURL(file);
        originalVideo.src = url;
        originalVideo.load();

        uploadSection.classList.add('hidden');
        appSection.classList.remove('hidden');

        // Upload to server
        upscaleBtn.disabled = true;
        upscaleBtn.innerHTML = spinnerSVG() + ' Uploading...';

        try {
            const formData = new FormData();
            formData.append('video', file);

            const res = await fetch('/api/upload', { method: 'POST', body: formData });
            const data = await res.json();

            if (!res.ok) throw new Error(data.error || 'Upload failed');

            state.taskId = data.task_id;
            state.videoInfo = data.info;
            populateInfo(data.info);
            updateOutputRes();

            upscaleBtn.disabled = false;
            upscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        } catch (err) {
            showToast('Upload failed: ' + err.message);
            upscaleBtn.disabled = false;
            upscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        }
    }

    function populateInfo(info) {
        $('#info-resolution').textContent = `${info.width} x ${info.height}`;
        $('#info-fps').textContent = info.fps;
        $('#info-duration').textContent = formatDuration(info.duration);
        $('#info-size').textContent = formatSize(info.size);
        $('#info-codec').textContent = info.codec.toUpperCase();
        $('#original-res').textContent = `${info.width}x${info.height}`;
    }

    function updateOutputRes() {
        if (!state.videoInfo) return;
        let w = state.videoInfo.width * state.selectedScale;
        let h = state.videoInfo.height * state.selectedScale;
        // Cap the longer dimension at 3840 (4K)
        const maxDim = 3840;
        if (w > maxDim || h > maxDim) {
            if (w >= h) { const r = maxDim / w; w = maxDim; h = Math.round(h * r); }
            else { const r = maxDim / h; h = maxDim; w = Math.round(w * r); }
        }
        h = h - (h % 2);
        w = w - (w % 2);
        $('#output-resolution').textContent = `${w} x ${h}`;
    }

    async function startUpscale() {
        if (state.processing || !state.taskId) return;
        state.processing = true;

        upscaleBtn.disabled = true;
        progressSection.classList.remove('hidden');
        downloadSection.classList.add('hidden');
        newVideoRow.classList.add('hidden');

        const startTime = Date.now();

        if (state.selectedMode === 'basic') {
            await runBasicUpscale(startTime);
        } else {
            await runProUpscale(startTime);
        }
    }

    // --- Basic Mode (server-side FFmpeg) ---
    async function runBasicUpscale(startTime) {
        try {
            const res = await fetch('/api/upscale/basic', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: state.taskId, scale: state.selectedScale }),
            });
            if (!res.ok) throw new Error('Failed to start upscale');

            // Listen to SSE progress
            const evtSource = new EventSource(`/api/progress/${state.taskId}`);
            evtSource.onmessage = (e) => {
                const data = JSON.parse(e.data);
                updateProgress(data, startTime);

                if (data.status === 'completed') {
                    evtSource.close();
                    onUpscaleComplete();
                } else if (data.status === 'error') {
                    evtSource.close();
                    onUpscaleError(data.message);
                }
            };
            evtSource.onerror = () => {
                evtSource.close();
                onUpscaleError('Connection lost');
            };
        } catch (err) {
            onUpscaleError(err.message);
        }
    }

    // --- Pro Mode (browser WebGPU) ---
    async function runProUpscale(startTime) {
        if (typeof ProUpscaler === 'undefined') {
            onUpscaleError('Pro upscaler module not loaded.');
            return;
        }

        try {
            updateProgress({ progress: 0, message: 'Loading AI model...', current_frame: 0, total_frames: state.videoInfo.duration * state.videoInfo.fps }, startTime);

            // Initialize Pro upscaler
            const upscaler = new ProUpscaler();
            await upscaler.init('/static/models/realesr-general-x4v3.onnx');
            const backend = upscaler.getBackend();
            updateProgress({ progress: 2, message: `Model loaded (${backend.toUpperCase()})`, current_frame: 0, total_frames: state.videoInfo.duration * state.videoInfo.fps }, startTime);

            // Tell server to prepare for frames
            await fetch('/api/pro/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: state.taskId }),
            });

            const video = document.createElement('video');
            video.muted = true;
            video.playsInline = true;
            video.src = URL.createObjectURL(state.file);
            await new Promise((resolve) => { video.onloadedmetadata = resolve; video.load(); });

            const fps = state.videoInfo.fps;
            const duration = video.duration;
            const totalFrames = Math.floor(duration * fps);
            const canvas = $('#frame-canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;

            let processed = 0;

            // Show tile progress within each frame
            upscaler.onTileProgress = (done, total) => {
                const framePct = done / total;
                const overallPct = Math.min(95, Math.round(((processed + framePct) / totalFrames) * 95));
                updateProgress({
                    progress: overallPct,
                    message: `AI upscaling frame ${processed + 1}/${totalFrames} (tile ${done}/${total}) [${backend.toUpperCase()}]`,
                    current_frame: processed,
                    total_frames: totalFrames,
                }, startTime);
            };

            // Upscale the current video frame and send it to the server.
            const handleOneFrame = async () => {
                ctx.drawImage(video, 0, 0, video.videoWidth, video.videoHeight);
                const imageData = ctx.getImageData(0, 0, video.videoWidth, video.videoHeight);

                // AI upscale (runs in Web Worker — UI stays responsive)
                const upscaled = await upscaler.processFrame(imageData);

                const outCanvas = $('#output-canvas');
                outCanvas.width = upscaled.width;
                outCanvas.height = upscaled.height;
                const outCtx = outCanvas.getContext('2d');
                outCtx.putImageData(upscaled, 0, 0);

                const blob = await new Promise((resolve) => outCanvas.toBlob(resolve, 'image/jpeg', 0.95));

                await fetch(`/api/pro/frame/${state.taskId}/${processed}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'image/jpeg' },
                    body: blob,
                });

                processed++;
                updateProgress({
                    progress: Math.min(95, Math.round((processed / totalFrames) * 95)),
                    message: `AI upscaling frame ${processed}/${totalFrames} [${backend.toUpperCase()}]`,
                    current_frame: processed,
                    total_frames: totalFrames,
                }, startTime);
            };

            // Seek to each frame individually. Slower than
            // requestVideoFrameCallback streaming, but frame-exact: rVFC
            // silently drops frames when the tab is occluded or throttled,
            // which corrupts the output. Extraction isn't the bottleneck —
            // AI inference is — so correctness wins.
            for (let i = 0; i < totalFrames; i++) {
                video.currentTime = i / fps;
                await new Promise((resolve) => { video.onseeked = resolve; });
                await handleOneFrame();
            }

            // Assemble final video
            updateProgress({ progress: 96, message: 'Assembling video with audio...', current_frame: totalFrames, total_frames: totalFrames }, startTime);

            await fetch('/api/pro/assemble', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: state.taskId, fps }),
            });

            // Poll for completion
            const pollInterval = setInterval(async () => {
                const res = await fetch(`/api/progress/${state.taskId}`);
                const reader = res.body.getReader();
                const { value } = await reader.read();
                reader.cancel();
                const text = new TextDecoder().decode(value);
                const match = text.match(/data: (.+)/);
                if (match) {
                    const data = JSON.parse(match[1]);
                    if (data.status === 'completed') {
                        clearInterval(pollInterval);
                        onUpscaleComplete();
                    } else if (data.status === 'error') {
                        clearInterval(pollInterval);
                        onUpscaleError(data.message);
                    }
                }
            }, 1000);

            URL.revokeObjectURL(video.src);
            upscaler.dispose();
        } catch (err) {
            onUpscaleError('Pro upscale failed: ' + err.message);
        }
    }

    function updateProgress(data, startTime) {
        const pct = data.progress || 0;
        progressFill.style.width = pct + '%';
        progressPercent.textContent = pct + '%';
        progressMessage.textContent = data.message || 'Processing...';
        progressFrames.textContent = `Frame ${data.current_frame || 0}/${data.total_frames || '?'}`;

        if (pct > 5 && startTime) {
            const elapsed = (Date.now() - startTime) / 1000;
            const estimated = (elapsed / pct) * (100 - pct);
            progressEta.textContent = 'ETA: ' + formatDuration(estimated);
        }
    }

    function onUpscaleComplete() {
        state.processing = false;
        progressSection.classList.add('hidden');
        downloadSection.classList.remove('hidden');
        newVideoRow.classList.remove('hidden');
        upscaleBtn.disabled = false;
        upscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';

        // Show upscaled video preview
        upscaledVideo.src = `/api/download/${state.taskId}`;
        upscaledVideo.classList.remove('hidden');
        outputPlaceholder.classList.add('hidden');
        $('#upscaled-res').textContent = $('#output-resolution').textContent.replace(' x ', 'x');

        downloadDetails.textContent = `${state.selectedMode === 'pro' ? 'AI' : 'FFmpeg'} upscaled to ${$('#output-resolution').textContent}`;
    }

    function onUpscaleError(msg) {
        state.processing = false;
        progressSection.classList.add('hidden');
        upscaleBtn.disabled = false;
        upscaleBtn.innerHTML = upscaleIconSVG() + ' Start Upscaling';
        showToast('Error: ' + msg);
    }

    function downloadResult() {
        if (!state.taskId) return;
        window.open(`/api/download/${state.taskId}`, '_blank');
    }

    function resetApp() {
        state.taskId = null;
        state.videoInfo = null;
        state.processing = false;
        state.file = null;

        uploadSection.classList.remove('hidden');
        appSection.classList.add('hidden');
        progressSection.classList.add('hidden');
        downloadSection.classList.add('hidden');
        newVideoRow.classList.add('hidden');

        originalVideo.src = '';
        upscaledVideo.src = '';
        upscaledVideo.classList.add('hidden');
        outputPlaceholder.classList.remove('hidden');
        fileInput.value = '';
    }

    // --- Utils ---
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

    function spinnerSVG() {
        return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 0.8s linear infinite"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"/><path d="M12 2a10 10 0 019.95 9" stroke-opacity="1"/></svg>';
    }

    function upscaleIconSVG() {
        return '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>';
    }

    // --- Sidebar Navigation ---
    function initSidebar() {
        document.querySelectorAll('.nav-item').forEach((item) => {
            item.addEventListener('click', () => {
                document.querySelectorAll('.nav-item').forEach((n) => n.classList.remove('active'));
                item.classList.add('active');
                const tab = item.dataset.tab;
                document.querySelectorAll('.tab-content').forEach((t) => t.classList.remove('active'));
                const target = document.getElementById('tab-' + tab);
                if (target) target.classList.add('active');
            });
        });
    }
})();
