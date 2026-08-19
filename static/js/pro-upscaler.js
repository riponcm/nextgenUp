/**
 * Pro Upscaler — thin wrapper that delegates to a Web Worker
 * so the main thread stays responsive during AI inference.
 */
class ProUpscaler {
    constructor() {
        this.worker = null;
        this.backend = 'unknown';
        this.onTileProgress = null;
        this._pendingReject = null;
    }

    /** Abort immediately: terminate the worker and reject any in-flight call. */
    cancel() {
        const reject = this._pendingReject;
        this._pendingReject = null;
        if (this.worker) {
            this.worker.terminate();
            this.worker = null;
        }
        if (reject) reject(new Error('cancelled'));
    }

    async init(modelPath) {
        this.worker = new Worker('/static/js/pro-worker.js');

        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('Model loading timed out')), 120000);

            this.worker.onmessage = (e) => {
                if (e.data.type === 'ready') {
                    clearTimeout(timeout);
                    this.backend = e.data.backend;
                    resolve();
                } else if (e.data.type === 'error') {
                    clearTimeout(timeout);
                    reject(new Error(e.data.message));
                }
            };

            this.worker.onerror = (e) => {
                clearTimeout(timeout);
                reject(new Error('Worker failed to load: ' + e.message));
            };

            this.worker.postMessage({ type: 'init', modelPath });
        });
    }

    getBackend() {
        return this.backend;
    }

    async processFrame(imageData) {
        return new Promise((resolve, reject) => {
            if (!this.worker) {
                reject(new Error('cancelled'));
                return;
            }
            this._pendingReject = reject;
            this.worker.onmessage = (e) => {
                if (e.data.type === 'frameResult') {
                    this._pendingReject = null;
                    resolve(new ImageData(
                        new Uint8ClampedArray(e.data.pixels),
                        e.data.width,
                        e.data.height
                    ));
                } else if (e.data.type === 'tileProgress' && this.onTileProgress) {
                    this.onTileProgress(e.data.done, e.data.total);
                } else if (e.data.type === 'error') {
                    this._pendingReject = null;
                    reject(new Error(e.data.message));
                }
            };

            const buffer = imageData.data.buffer.slice(0);
            this.worker.postMessage(
                { type: 'frame', pixels: buffer, width: imageData.width, height: imageData.height },
                [buffer]
            );
        });
    }

    dispose() {
        if (this.worker) {
            this.worker.postMessage({ type: 'dispose' });
            setTimeout(() => { if (this.worker) { this.worker.terminate(); this.worker = null; } }, 500);
        }
    }
}
