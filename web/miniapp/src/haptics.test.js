import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import test from 'node:test';

const effects = readFileSync(new URL('../../static/js/effects.js', import.meta.url), 'utf8');
function launch({ version = 8, signed = true, saved = new Map() } = {}) {
    const calls = [];
    const telegram = { initData:signed ? 'fixture' : '', isVersionAtLeast:min => version >= Number(min),
        HapticFeedback: { selectionChanged:() => calls.push('selection'), impactOccurred:k => calls.push('impact:' + k),
            notificationOccurred:k => calls.push('notification:' + k) } };
    const document = { hidden:false };
    const window = { Telegram:{ WebApp:telegram } };
    runInNewContext(effects, { window, document, localStorage:{ getItem:k => saved.get(k), setItem:(k,v) => saved.set(k,v) } });
    return { calls, feedback:window.kkepikHaptics, document, telegram };
}
test('sends selection, impact and result through the matching Telegram API', () => {
    const { calls, feedback } = launch();
    for (const kind of ['selection','soft','light','medium','success','warning','error']) feedback.play(kind);
    assert.deepEqual(calls, ['selection','impact:soft','impact:light','impact:medium','notification:success','notification:warning','notification:error']);
});
test('respects the device preference on the next launch and stays silent in the background', () => {
    const saved = new Map();
    launch({saved}).feedback.setEnabled(false);
    const { calls, feedback, document, telegram } = launch({saved});
    feedback.play();
    feedback.setEnabled(true);
    document.hidden = true; feedback.play();
    document.hidden = false; telegram.isActive = false; feedback.play();
    telegram.isActive = true; feedback.play('light');
    assert.deepEqual(calls, ['impact:light']);
});
test('a browser or older Telegram can complete actions without haptics', () => {
    for (const options of [{version:6},{signed:false}]) {
        const { feedback, calls } = launch(options);
        feedback.play('success');
        assert.equal(feedback.supported, false);
        assert.deepEqual(calls, []);
    }
});
