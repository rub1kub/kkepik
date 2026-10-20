(function (root) {
    'use strict';

    // One bounded board, one read cursor, one active read. Camera movement
    // never changes the synchronization scope or cancels a useful snapshot.
    class PixelState {
        constructor(bounds, timeoutMs = 6000) {
            this.bounds = bounds;
            this.pixels = new Map();
            this.revision = 0;
            this.pending = null;
            this.timeoutMs = timeoutMs;
        }

        get busy() { return this.pending !== null; }

        *visible(area, pixels = this.pixels) {
            // Sparse backgrounds need only a short scan; zoomed-in views of
            // dense artwork need only the visible cells, not the whole board.
            const count = (area.x1 - area.x0) * (area.y1 - area.y0);
            if (pixels.size <= count) {
                for (const pixel of pixels.values()) {
                    if (pixel[0] >= area.x0 && pixel[0] < area.x1
                        && pixel[1] >= area.y0 && pixel[1] < area.y1) yield pixel;
                }
            } else {
                for (let y = area.y0; y < area.y1; y++) {
                    for (let x = area.x0; x < area.x1; x++) {
                        const pixel = pixels.get(x + ',' + y);
                        if (pixel) yield pixel;
                    }
                }
            }
        }

        validPixel(pixel, bounds = this.bounds, revision = Infinity) {
            return Array.isArray(pixel) && pixel.length === 4
                && pixel.every(Number.isSafeInteger)
                && pixel[0] >= bounds.x0 && pixel[0] < bounds.x1
                && pixel[1] >= bounds.y0 && pixel[1] < bounds.y1
                && pixel[2] >= -1 && pixel[2] < 16
                && pixel[3] >= 0 && pixel[3] <= revision;
        }

        apply(pixel) {
            if (!this.validPixel(pixel)) return;
            const key = pixel[0] + ',' + pixel[1];
            const previous = this.pixels.get(key);
            if (!previous || pixel[3] >= previous[3]) this.pixels.set(key, pixel);
            // A write acknowledgment must not skip unread edits from others.
        }

        merge(data, since) {
            const bounds = data.bounds;
            if (!data.success || typeof data.full !== 'boolean'
                || !Number.isSafeInteger(data.revision) || data.revision < 0
                || (!data.full && data.revision < since)
                || !bounds || !['x0', 'y0', 'x1', 'y1'].every(k => Number.isSafeInteger(bounds[k]))
                || bounds.x0 >= bounds.x1 || bounds.y0 >= bounds.y1
                || (bounds.x1 - bounds.x0) * (bounds.y1 - bounds.y0) > 65536
                || !Array.isArray(data.pixels)
                || data.pixels.length > (bounds.x1 - bounds.x0) * (bounds.y1 - bounds.y0)
                || !data.pixels.every(p => this.validPixel(p, bounds, data.revision))) {
                throw new Error('Некорректный ответ полотна.');
            }
            if (data.full) {
                for (const [key, pixel] of this.pixels) {
                    // Keep writes confirmed after this snapshot was taken.
                    // A restored server database instead needs a fresh cache.
                    if (data.revision < since || pixel[3] <= data.revision) this.pixels.delete(key);
                }
            }
            Object.assign(this.bounds, bounds);
            for (const [key, pixel] of this.pixels) {
                if (!this.validPixel(pixel)) this.pixels.delete(key);
            }
            data.pixels.forEach(pixel => this.apply(pixel));
            this.revision = data.revision;
        }

        read(request) {
            if (this.pending) return this.pending.promise;
            const controller = new AbortController();
            const own = { controller, since: this.revision };
            this.pending = own;
            let timeout;
            const cancelled = new Promise(resolve => { own.cancel = () => resolve(null); });
            const deadline = new Promise((_, reject) => {
                timeout = setTimeout(() => {
                    controller.abort();
                    reject(new Error('Полотно не ответило вовремя.'));
                }, this.timeoutMs);
            });
            const network = Promise.resolve().then(() => {
                if (this.pending !== own) return null;
                return request({ ...this.bounds, since: own.since }, controller.signal);
            });
            own.promise = Promise.race([network, cancelled, deadline]).then(data => {
                if (!data || this.pending !== own || controller.signal.aborted) return null;
                this.merge(data, own.since);
                return data;
            }).catch(error => {
                if (this.pending !== own) return null;
                throw error;
            }).finally(() => {
                clearTimeout(timeout);
                if (this.pending === own) this.pending = null;
            });
            return own.promise;
        }

        cancel() {
            const own = this.pending;
            this.pending = null;
            if (own) { own.cancel(); own.controller.abort(); }
        }
    }

    if (typeof module !== 'undefined' && module.exports) module.exports = PixelState;
    else root.KepikPixelState = PixelState;
}(typeof globalThis !== 'undefined' ? globalThis : this));
