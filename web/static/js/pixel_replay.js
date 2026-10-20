(function (root) {
    'use strict';
    const stride = 9;
    class ReplayEngine {
        constructor(buffer, bounds, palette = ['#faf8f3','#dddcd6','#92958f','#343b3e','#d6a2b3','#bb716a','#ce9b68','#98785f','#d4bf79','#afbd8c','#789b82','#80aaa6','#82a9c1','#6f85af','#b09bbe','#887795']) {
            if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 8 || (buffer.byteLength - 8) % (stride * 4)
                || new TextDecoder().decode(new Uint8Array(buffer, 0, 8)) !== 'KKEPBR01') throw new Error('Неверный архив таймлапса.');
            this.records = new Int32Array(buffer, 8);
            this.bounds = bounds;
            this.width = bounds.x1-bounds.x0;
            this.rgb = palette.map(color => [1,3,5].map(at => parseInt(color.slice(at,at+2),16)));
            this.imageBuffer = new Uint8ClampedArray(this.width*(bounds.y1-bounds.y0)*4);
            const count = this.records.length / stride;
            if (count > 1000000) throw new Error('Архив слишком большой.');
            this.offsets = new Uint32Array(count + 1);
            this.steps = 0;
            this.baseline = 0;
            let index = 0;
            while (index < count) {
                const at = index * stride, source = this.records[at + 8];
                const revision = this.records[at + 1];
                this.offsets[this.steps] = index;
                if (source === 2) {
                    if (this.steps !== this.baseline) throw new Error('Неверное начальное состояние.');
                    this.baseline++;
                }
                index++;
                if (source === 3) {
                    while (index < count && this.records[index * stride + 8] === 3 && this.records[index * stride + 1] === revision) index++;
                }
                this.steps++;
            }
            this.offsets[this.steps] = count;
            this.changes = this.steps - this.baseline;
            this.pixels = new Map();
            this.reset();
        }
        applyStep(step) {
            for (let row = this.offsets[step]; row < this.offsets[step + 1]; row++) {
                const offset = row * stride, r = this.records, b = this.bounds;
                const x0 = Math.max(b.x0, r[offset + 3]), y0 = Math.max(b.y0, r[offset + 4]);
                const x1 = Math.min(b.x1, r[offset + 5]), y1 = Math.min(b.y1, r[offset + 6]);
                const color = r[offset + 7], revision = r[offset + 1];
                if (color < -1 || color > 15) throw new Error('Неверный цвет в архиве.');
                for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
                    const key = x + ',' + y;
                    if (color < 0) this.pixels.delete(key);
                    else this.pixels.set(key, [x, y, color, revision]);
                    const offset = ((y-b.y0)*this.width+x-b.x0)*4, rgb = this.rgb[Math.max(0,color)];
                    this.imageBuffer[offset]=rgb[0];this.imageBuffer[offset+1]=rgb[1];this.imageBuffer[offset+2]=rgb[2];this.imageBuffer[offset+3]=255;
                }
            }
        }
        reset() {
            this.pixels.clear();
            const paper=this.rgb[0];
            for(let offset=0;offset<this.imageBuffer.length;offset+=4){this.imageBuffer[offset]=paper[0];this.imageBuffer[offset+1]=paper[1];this.imageBuffer[offset+2]=paper[2];this.imageBuffer[offset+3]=255;}
            for (let i = 0; i < this.baseline; i++) this.applyStep(i);
            this.position = 0;
        }
        advance(target, deadline = Infinity) {
            target = Math.max(0, Math.min(this.changes, Math.round(target)));
            if (target < this.position) this.reset();
            while (this.position < target && performance.now() < deadline) {
                this.applyStep(this.baseline + this.position);
                this.position++;
            }
            return this.position === target;
        }
        get atOffset() {
            const step = this.baseline + this.position;
            const row = step ? this.offsets[step] - 1 : 0;
            return this.records[row * stride + 2] || 0;
        }
    }
    root.KepikReplayEngine = ReplayEngine;
    if (typeof module !== 'undefined' && module.exports) module.exports = ReplayEngine;
    if (!root.document) return;

    root.KepikCanvasReplay = {
        create({ onChange, onFit }) {
            const $ = id => document.getElementById(id);
            const panel = $('pixelReplay'), heading = $('pixelReplayHeading');
            const playButton = $('pixelReplayPlay'), timeline = $('pixelReplayTimeline');
            const speedControl = $('pixelReplaySpeed'), speedLabel = $('pixelReplaySpeedLabel');
            const progress = $('pixelReplayProgress'), summary = $('pixelReplaySummary');
            const notice = $('pixelReplayNotice'), fit = $('pixelReplayFit'), retry = $('pixelReplayRetry');
            let opened = false, engine = null, manifest = null, controller = null;
            const image = document.createElement('canvas'), imageContext = image.getContext('2d');
            let imageData=null;
            let raf = null, playing = false, generation = 0, speed = 3000, last = 0, remainder = 0;
            function changed(){if(imageData)imageContext.putImageData(imageData,0,0);onChange();}

            function update() {
                playButton.disabled = !engine || !engine.changes;
                timeline.disabled = !engine || !engine.changes;
                playButton.setAttribute('aria-label', playing ? 'Пауза' : 'Воспроизвести таймлапс');
                $('pixelReplayPlayIcon').setAttribute('d', playing ? 'M8 5v14M16 5v14' : 'm8 5 11 7-11 7Z');
                speedLabel.textContent = speed.toLocaleString('ru-RU') + ' изменений/с';
                speedControl.setAttribute('aria-valuetext', speedLabel.textContent);
                panel.querySelectorAll('[data-replay-speed]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.replaySpeed) === speed)));
                if (engine) {
                    timeline.max = String(engine.changes);
                    timeline.value = String(engine.position);
                    progress.textContent = engine.position.toLocaleString('ru-RU') + ' / ' + engine.changes.toLocaleString('ru-RU');
                    timeline.setAttribute('aria-valuetext', progress.textContent + ' изменений');
                    notice.textContent = engine.position === engine.changes ? 'Итоговое полотно' : new Date(manifest.history_started_at_ms + engine.atOffset)
                        .toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' МСК';
                }
            }
            function pause() {
                playing = false;
                generation++;
                if (raf !== null) cancelAnimationFrame(raf);
                raf = null; remainder = 0;
                update();
            }
            function tick(now) {
                raf = null;
                if (!opened || !playing || document.hidden || !engine) return;
                const elapsed = Math.min(250, Math.max(0, now - last)); last = now;
                remainder = Math.min(speed / 4, remainder + elapsed * speed / 1000);
                const before = engine.position, target = Math.min(engine.changes, before + Math.floor(remainder));
                engine.advance(target, performance.now() + 8);
                remainder -= engine.position - before;
                if (engine.position !== before) { update(); changed(); }
                if (engine.position >= engine.changes) { pause(); return; }
                raf = requestAnimationFrame(tick);
            }
            function play() {
                if (!engine || !opened) return;
                pause();
                if (engine.position === engine.changes) { engine.reset(); changed(); }
                playing = true; last = performance.now(); update(); raf = requestAnimationFrame(tick);
            }
            function seek(target) {
                pause();
                const own = generation;
                const batch = () => {
                    if (!opened || own !== generation) return;
                    const complete = engine.advance(target, performance.now() + 8);
                    update(); changed();
                    if (!complete) raf = requestAnimationFrame(batch); else raf = null;
                };
                batch();
            }
            async function load() {
                if (engine || controller) return;
                const own = new AbortController(); controller = own;
                const timeout = setTimeout(() => own.abort(), 30000);
                notice.textContent = 'Загружаем таймлапс…'; retry.hidden = true;
                try {
                    const headers = { 'X-Telegram-Init-Data': root.Telegram.WebApp.initData };
                    const response = await fetch('/api/pixels/replay', { headers, signal: own.signal, cache: 'no-store' });
                    const data = await response.json();
                    if (!response.ok || !data.success) throw new Error(data.error || 'Не удалось загрузить итоги.');
                    const url = new URL(data.data_url, location.origin);
                    if (url.origin !== location.origin || url.pathname !== '/api/pixels/replay/data') throw new Error('Неверный адрес архива.');
                    const binary = await fetch(url.href, { headers, signal: own.signal, cache: 'no-store' });
                    if (!binary.ok) throw new Error('Не удалось загрузить таймлапс.');
                    const buffer = await binary.arrayBuffer();
                    if (own.signal.aborted || controller !== own || !opened) return;
                    if (data.format !== 'kkepik-pixel-history-v1' || data.stride !== stride || buffer.byteLength !== 8 + data.record_count * stride * 4) throw new Error('Неполный архив таймлапса.');
                    if (root.crypto?.subtle) {
                        const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), value => value.toString(16).padStart(2, '0')).join('');
                        if (hash !== data.sha256) throw new Error('Архив повреждён. Повторите загрузку.');
                    }
                    if (own.signal.aborted || controller !== own || !opened) return;
                    manifest = data; engine = new ReplayEngine(buffer, data.bounds, data.palette);
                    image.width=data.bounds.x1-data.bounds.x0;image.height=data.bounds.y1-data.bounds.y0;
                    imageData=new ImageData(engine.imageBuffer,image.width,image.height);
                    if (engine.changes !== data.stats.changes) throw new Error('Неполный архив таймлапса.');
                    summary.textContent = data.stats.participants.toLocaleString('ru-RU') + ' участников · ' + engine.changes.toLocaleString('ru-RU') + ' изменений';
                    update(); onFit(); changed(); play();
                } catch (error) {
                    if (controller === own && opened) {
                        engine = null; manifest = null; notice.textContent = own.signal.aborted ? 'Загрузка не завершилась. Попробуйте ещё раз.' : error.message;
                        retry.hidden = false; update();
                    }
                } finally {
                    clearTimeout(timeout); if (controller === own) controller = null;
                }
            }
            playButton.addEventListener('click', () => playing ? pause() : play());
            timeline.addEventListener('input', () => { if (engine) seek(Number(timeline.value)); });
            speedControl.addEventListener('input', () => { speed = Math.round(Math.pow(10, Number(speedControl.value) / 100)); last = performance.now(); remainder = 0; update(); });
            panel.querySelectorAll('[data-replay-speed]').forEach(button => button.addEventListener('click', () => {
                speed = Number(button.dataset.replaySpeed); speedControl.value = String(Math.round(Math.log10(speed) * 100));
                last = performance.now(); remainder = 0; update();
            }));
            fit.addEventListener('click', onFit); retry.addEventListener('click', load);
            document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
            root.addEventListener('pagehide', () => { pause(); if (controller) controller.abort(); });
            update();
            return {
                get active() { return opened; }, get loaded() { return Boolean(engine); },
                get pixels() { return engine?.pixels; },
                get image() { return image; },
                open() {
                    if (opened) return;
                    opened = true; panel.hidden = false; heading.hidden = false;
                    document.body.classList.add('pixel-replaying');
                    $('pixelDone').setAttribute('aria-label', 'Вернуться на главную');
                    onFit(); if (engine) { update(); play(); } else load();
                },
                close() {
                    opened = false; pause(); if (controller) { controller.abort(); controller = null; }
                    panel.hidden = true; heading.hidden = true; document.body.classList.remove('pixel-replaying');
                    $('pixelDone').setAttribute('aria-label', 'Завершить рисование'); onChange();
                }, pause,
            };
        },
    };
}(typeof window === 'undefined' ? globalThis : window));
