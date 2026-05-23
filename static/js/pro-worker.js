/**
 * Pro Upscaler Web Worker — runs ONNX inference off the main thread.
 */
importScripts('https://cdn.jsdelivr.net/npm/onnxruntime-web@1.21.0/dist/ort.all.min.js');

let session = null;
let inputName = 'input';
let outputName = 'output';
let tileSize = 64;
let tilePad = 8;
let backend = 'wasm';
const scale = 4;

self.onmessage = async (e) => {
    const msg = e.data;
    try {
        switch (msg.type) {
            case 'init': await handleInit(msg); break;
            case 'frame': await handleFrame(msg); break;
            case 'dispose': handleDispose(); break;
        }
    } catch (err) {
        self.postMessage({ type: 'error', message: err.message });
    }
};

async function handleInit(msg) {
    ort.env.wasm.wasmPaths = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.21.0/dist/';
    ort.env.wasm.numThreads = 1;

    const strategies = [];
    if (typeof navigator !== 'undefined' && navigator.gpu) {
        try {
            const adapter = await navigator.gpu.requestAdapter();
            if (adapter) strategies.push(['webgpu']);
        } catch {}
    }
    strategies.push(['wasm']);

    for (const providers of strategies) {
        try {
            session = await ort.InferenceSession.create(msg.modelPath, {
                executionProviders: providers,
                graphOptimizationLevel: 'all',
            });

            inputName = session.inputNames[0];
            outputName = session.outputNames[0];

            // Warmup
            const testTensor = new ort.Tensor('float32', new Float32Array(3 * 16 * 16), [1, 3, 16, 16]);
            const feeds = {};
            feeds[inputName] = testTensor;
            await session.run(feeds);

            backend = providers[0];

            if (backend === 'webgpu') {
                tileSize = 128;
                tilePad = 12;
            }

            self.postMessage({ type: 'ready', backend });
            return;
        } catch (err) {
            if (session) { try { session.release(); } catch {} session = null; }
        }
    }

    throw new Error('No working inference backend found');
}

async function handleFrame(msg) {
    const pixels = new Uint8ClampedArray(msg.pixels);
    const width = msg.width;
    const height = msg.height;
    const outW = width * scale;
    const outH = height * scale;

    const outPixels = new Uint8ClampedArray(outW * outH * 4);

    const tilesX = Math.ceil(width / tileSize);
    const tilesY = Math.ceil(height / tileSize);
    const totalTiles = tilesX * tilesY;
    let doneTiles = 0;

    for (let ty = 0; ty < tilesY; ty++) {
        for (let tx = 0; tx < tilesX; tx++) {
            const srcX = tx * tileSize;
            const srcY = ty * tileSize;

            const x0 = Math.max(0, srcX - tilePad);
            const y0 = Math.max(0, srcY - tilePad);
            const x1 = Math.min(width, srcX + tileSize + tilePad);
            const y1 = Math.min(height, srcY + tileSize + tilePad);
            const tileW = x1 - x0;
            const tileH = y1 - y0;

            // Extract tile
            const tileData = new Float32Array(3 * tileH * tileW);
            const ch = tileH * tileW;
            for (let row = 0; row < tileH; row++) {
                for (let col = 0; col < tileW; col++) {
                    const srcIdx = ((y0 + row) * width + (x0 + col)) * 4;
                    const dstIdx = row * tileW + col;
                    tileData[dstIdx] = pixels[srcIdx] / 255.0;
                    tileData[ch + dstIdx] = pixels[srcIdx + 1] / 255.0;
                    tileData[ch * 2 + dstIdx] = pixels[srcIdx + 2] / 255.0;
                }
            }

            const inputTensor = new ort.Tensor('float32', tileData, [1, 3, tileH, tileW]);
            const feeds = {};
            feeds[inputName] = inputTensor;
            const results = await session.run(feeds);
            const out = results[outputName];

            const outShape = out.dims;
            const isNHWC = outShape.length === 4 && outShape[3] <= 4 && outShape[1] > 4;

            const padL = (srcX - x0) * scale;
            const padT = (srcY - y0) * scale;
            const copyW = Math.min(tileSize, width - srcX) * scale;
            const copyH = Math.min(tileSize, height - srcY) * scale;
            const oTileW = tileW * scale;
            const oTileH = tileH * scale;

            if (isNHWC) {
                for (let row = 0; row < copyH; row++) {
                    for (let col = 0; col < copyW; col++) {
                        const si = ((padT + row) * oTileW + (padL + col)) * 3;
                        const di = ((srcY * scale + row) * outW + (srcX * scale + col)) * 4;
                        outPixels[di]     = clamp(out.data[si] * 255);
                        outPixels[di + 1] = clamp(out.data[si + 1] * 255);
                        outPixels[di + 2] = clamp(out.data[si + 2] * 255);
                        outPixels[di + 3] = 255;
                    }
                }
            } else {
                const chs = oTileW * oTileH;
                for (let row = 0; row < copyH; row++) {
                    for (let col = 0; col < copyW; col++) {
                        const si = (padT + row) * oTileW + (padL + col);
                        const di = ((srcY * scale + row) * outW + (srcX * scale + col)) * 4;
                        outPixels[di]     = clamp(out.data[si] * 255);
                        outPixels[di + 1] = clamp(out.data[chs + si] * 255);
                        outPixels[di + 2] = clamp(out.data[chs * 2 + si] * 255);
                        outPixels[di + 3] = 255;
                    }
                }
            }

            doneTiles++;
            self.postMessage({ type: 'tileProgress', done: doneTiles, total: totalTiles });
        }
    }

    self.postMessage(
        { type: 'frameResult', pixels: outPixels.buffer, width: outW, height: outH },
        [outPixels.buffer]
    );
}

function handleDispose() {
    if (session) { try { session.release(); } catch {} session = null; }
    self.postMessage({ type: 'disposed' });
}

function clamp(v) { return Math.min(255, Math.max(0, Math.round(v))); }
