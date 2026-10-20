const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../static/js/miniapp_presence.js'),'utf8');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
function fixture({publicPage=false,held=false}={}){
    let now=0,serial=0;const timers=new Map(),calls=[],pending=[];
    const eventTarget=()=>({listeners:{},addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);},dispatch(name){for(const fn of this.listeners[name]||[])fn();}});
    const document=eventTarget();document.hidden=false;
    const window=eventTarget();const sdk={};
    const setTimeout=(fn,ms)=>{const id=++serial;timers.set(id,{fn,at:now+ms});return id;};
    const navigator={onLine:true};
    const sandbox={window,document,navigator,AbortController,Date,Math,crypto:{randomUUID:()=> 'session-one'},setTimeout,clearTimeout:id=>timers.delete(id),CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},fetch:(url,options)=>{
        calls.push({url,...options,body:JSON.parse(options.body)});
        const response={ok:true,json:async()=>({success:true,online_count:3,online_at_ms:now+1000})};
        if(held && !options.keepalive)return new Promise(resolve=>pending.push(()=>resolve(response)));
        return Promise.resolve(response);
    }};
    window.crypto=sandbox.crypto;window.dispatchEvent=()=>{};
    window.Telegram={WebApp:{initData:publicPage?'':'signed-launch-data',onEvent(name,fn){sdk[name]=fn;}}};
    vm.runInNewContext(code,sandbox);
    async function tick(ms){const end=now+ms;await flush();let attempts=0;while(true){const first=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!first)break;assert(++attempts<100);now=first[1].at;timers.delete(first[0]);first[1].fn();await flush();}now=end;await flush();}
    return {window,document,navigator,sdk,calls,pending,tick};
}
test('all mini-app pages register before visiting the canvas; public pages do not',async()=>{
    const app=fixture();await app.tick(0);assert.equal(app.calls.length,1);
    assert.equal(app.calls[0].body.active,true);assert.equal(app.window.KepikPresence.count,3);
    assert.equal(app.calls[0].headers['X-Telegram-Init-Data'],'signed-launch-data');
    await app.tick(15000);assert.equal(app.calls.length,2);assert.equal(app.calls[1].body.sequence,2);
    const publicApp=fixture({publicPage:true});await publicApp.tick(40000);assert.equal(publicApp.calls.length,0);
});
test('hidden page sends a higher-sequence leave, then resumes without accumulating timers',async()=>{
    const app=fixture();await app.tick(0);app.document.hidden=true;app.document.dispatch('visibilitychange');
    assert.equal(app.calls[1].keepalive,true);assert.equal(app.calls[1].body.active,false);
    assert.equal(app.calls[1].body.sequence,2);assert.equal(app.window.KepikPresence.count,null);
    await app.tick(45000);assert.equal(app.calls.length,2);
    app.document.hidden=false;app.document.dispatch('visibilitychange');app.window.dispatch('pageshow');await app.tick(0);
    assert.equal(app.calls.length,3);assert.equal(app.calls[2].body.sequence,3);assert.equal(app.calls[2].body.active,true);
    await app.tick(15000);assert.equal(app.calls.length,4);
});
test('late heartbeat cannot revive a closed page or interfere with its resumed request',async()=>{
    const app=fixture({held:true});await app.tick(0);app.window.dispatch('pagehide');
    assert.equal(app.calls[0].signal.aborted,true);assert.equal(app.calls[1].body.active,false);
    app.window.dispatch('pageshow');await app.tick(0);assert.equal(app.calls.length,3);
    app.pending[0]();await flush();assert.equal(app.window.KepikPresence.count,null);
    app.pending[1]();await flush();assert.equal(app.window.KepikPresence.count,3);
    await app.tick(15000);assert.equal(app.calls.length,4);
});
test('Telegram deactivation unregisters a page even if the document is still visible',async()=>{
    const app=fixture();await app.tick(0);app.sdk.deactivated();await app.tick(30000);
    assert.equal(app.calls.length,2);assert.equal(app.calls[1].body.active,false);
    app.sdk.activated();await app.tick(0);assert.equal(app.calls.length,3);
});
test('offline requests stop, and reconnect resumes with a new sequence',async()=>{
    const app=fixture();await app.tick(0);app.navigator.onLine=false;app.window.dispatch('offline');await app.tick(35000);
    assert.equal(app.calls.length,1);assert.equal(app.window.KepikPresence.count,null);
    app.navigator.onLine=true;app.window.dispatch('online');await app.tick(0);
    assert.equal(app.calls.length,2);assert.equal(app.calls[1].body.sequence,2);
});
