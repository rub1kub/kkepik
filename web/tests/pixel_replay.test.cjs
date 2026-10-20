const test = require('node:test');
const assert = require('node:assert/strict');
const ReplayEngine = require('../static/js/pixel_replay.js');
const bounds = { x0: -2, y0: -2, x1: 2, y1: 2 };
function buffer(rows) {
    const data = new ArrayBuffer(8 + rows.length * 36);
    new Uint8Array(data, 0, 8).set(new TextEncoder().encode('KKEPBR01'));
    new Int32Array(data, 8).set(rows.flat());
    return data;
}
const history = [
    [1, 1, 0, -1, -1, 0, 0, 4, 2],
    [2, 2, 10, 0, 0, 1, 1, 3, 0],
    [3, 3, 20, -2, -2, 2, 2, 8, 1],
    [4, 4, 30, 0, 0, 1, 1, -1, 3],
    [5, 4, 30, -1, -1, 0, 0, 4, 3],
];
test('baseline is present before playback and moderation is applied in order', () => {
    const engine = new ReplayEngine(buffer(history), bounds);
    assert.equal(engine.changes, 3);
    assert.equal(engine.position, 0);
    assert.equal(engine.pixels.get('-1,-1')[2], 4);
    engine.advance(2);
    assert.equal(engine.pixels.size, 16);
    assert.equal(engine.pixels.get('-1,-1')[2], 8);
});
test('a rollback is one atomic step, including erasures and restored cells', () => {
    const engine = new ReplayEngine(buffer(history), bounds);
    engine.advance(3);
    assert.equal(engine.position, 3);
    assert.equal(engine.pixels.has('0,0'), false);
    assert.equal(engine.pixels.get('-1,-1')[2], 4);
    assert.equal(engine.atOffset, 30);
});
test('backward and forward seeks produce the same state as uninterrupted playback', () => {
    const engine = new ReplayEngine(buffer(history), bounds);
    engine.advance(3); const expected = [...engine.pixels.entries()];
    engine.advance(1);
    assert.equal(engine.pixels.size, 2);
    engine.advance(3);
    assert.deepEqual([...engine.pixels.entries()], expected);
    engine.advance(-100); assert.equal(engine.position, 0);
    engine.advance(999); assert.equal(engine.position, 3);
});
test('a frame budget stops between logical edits without skipping any', () => {
    const engine = new ReplayEngine(buffer(history), bounds);
    assert.equal(engine.advance(3, -1), false);
    assert.equal(engine.position, 0);
    assert.equal(engine.advance(3), true);
});
test('malformed or truncated files cannot be played', () => {
    assert.throws(() => new ReplayEngine(new ArrayBuffer(7), bounds));
    assert.throws(() => new ReplayEngine(new ArrayBuffer(48), bounds));
    assert.throws(() => new ReplayEngine(buffer([history[1], history[0]]), bounds));
});
