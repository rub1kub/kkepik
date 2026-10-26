const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const PixelState = require('../static/js/pixel_state.js');
const bounds = () => ({x0:-64,y0:-128,x1:64,y1:128});
const colors = ['#faf8f3','#dddcd6','#92958f','#343b3e','#d6a2b3','#bb716a','#ce9b68','#98785f','#d4bf79','#afbd8c','#789b82','#80aaa6','#82a9c1','#6f85af','#b09bbe','#887795'];
const snapshot = (revision, pixels=[], full=true) => ({success:true,bounds:bounds(),revision,pixels,full,palette:colors,remaining_ms:0});
const deferred = () => {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush = async () => {for(let i=0;i<16;i++)await Promise.resolve();};

test('one active read, covering the entire finite board',async()=>{
    const state=new PixelState(bounds()), gate=deferred();let requests=0;
    const request=(area)=>{requests++;assert.deepEqual(area,{...bounds(),since:0});return gate.promise;};
    const first=state.read(request),second=state.read(request);
    assert.equal(first,second);await flush();assert.equal(requests,1);
    gate.resolve(snapshot(1,[[63,127,2,1]]));await first;
    assert.equal(state.pixels.size,1);assert.equal(state.busy,false);
});

test('write acknowledgments preserve unread edits by other players',async()=>{
    const state=new PixelState(bounds());await state.read(()=>snapshot(10,[[0,0,3,10]]));
    state.apply([0,0,12,12]);assert.equal(state.revision,10);
    await state.read(area=>{assert.equal(area.since,10);return snapshot(12,[[4,3,2,11],[0,0,12,12]],false);});
    assert.equal(state.pixels.get('4,3')[3],11);assert.equal(state.pixels.get('0,0')[2],12);
});

test('a full snapshot taken before a write cannot erase that confirmed write',async()=>{
    const state=new PixelState(bounds()),gate=deferred();const read=state.read(()=>gate.promise);
    await flush();state.apply([30,0,5,12]);gate.resolve(snapshot(11,[[0,0,2,11]]));await read;
    assert.equal(state.pixels.get('30,0')[3],12);assert.equal(state.revision,11);
});

test('old updates cannot overwrite a newer pixel or revive an erased cell',async()=>{
    const state=new PixelState(bounds());state.apply([0,0,-1,30]);state.apply([0,0,2,20]);
    await state.read(()=>snapshot(29,[[0,0,5,29]]));
    assert.equal(state.pixels.get('0,0')[2],-1);
    await state.read(()=>snapshot(31,[[0,0,6,31]],false));
    assert.equal(state.pixels.get('0,0')[2],6);
});

for(const lateFailure of [false,true])test(`cancelled ${lateFailure?'failure':'response'} cannot affect a newer read`,async()=>{
    const state=new PixelState(bounds()),old=deferred(),fresh=deferred();let signal;
    const first=state.read((_,s)=>{signal=s;return old.promise;});await flush();state.cancel();
    const second=state.read(()=>fresh.promise);await flush();assert.equal(signal.aborted,true);
    assert.equal(await first,null);assert.equal(state.busy,true);
    if(lateFailure)old.reject(new Error('late failure'));else old.resolve(snapshot(20,[[0,0,1,20]]));
    await flush();assert.equal(state.pixels.size,0);assert.equal(state.busy,true);
    fresh.resolve(snapshot(21,[[0,0,4,21]]));await second;
    assert.equal(state.pixels.get('0,0')[2],4);assert.equal(state.revision,21);
});

test('cancellation before dispatch avoids sending the request',async()=>{
    const state=new PixelState(bounds());let requests=0;
    const read=state.read(()=>{requests++;return snapshot(1);});state.cancel();
    assert.equal(await read,null);assert.equal(requests,0);
});

test('timeout releases a stuck read even if transport ignores abort',async()=>{
    const state=new PixelState(bounds(),15),gate=deferred();let signal;
    await assert.rejects(state.read((_,s)=>{signal=s;return gate.promise;}),/вовремя/);
    assert.equal(signal.aborted,true);assert.equal(state.busy,false);
    await state.read(()=>snapshot(2,[[0,0,2,2]]));gate.resolve(snapshot(1,[[0,0,1,1]]));await flush();
    assert.equal(state.pixels.get('0,0')[2],2);assert.equal(state.revision,2);
});

test('invalid snapshots fail atomically without clearing known artwork',async()=>{
    const state=new PixelState(bounds());await state.read(()=>snapshot(3,[[0,0,2,3]]));
    for(const invalid of [snapshot(4,[[64,0,1,4]]),snapshot(4,[[0,0,16,4]]),snapshot(4,[[0,0,2,5]]),snapshot(2,[],false),{...snapshot(4),pixels:null}]){
        await assert.rejects(state.read(()=>invalid),/Некорректный/);
        assert.equal(state.pixels.get('0,0')[2],2);assert.equal(state.revision,3);
    }
});

test('database rollback restores a clean cache instead of retaining future revisions',async()=>{
    const state=new PixelState(bounds());await state.read(()=>snapshot(12,[[0,0,5,12]]));
    state.apply([3,3,8,13]);await state.read(area=>{assert.equal(area.since,12);return snapshot(4,[[0,0,1,4]]);});
    assert.equal(state.pixels.size,1);assert.equal(state.pixels.get('0,0')[2],1);assert.equal(state.revision,4);
});

test('storage remains bounded even for a completely filled board',async()=>{
    const state=new PixelState(bounds()),pixels=[];
    for(let y=-128;y<128;y++)for(let x=-64;x<64;x++)pixels.push([x,y,2,1]);
    await state.read(()=>snapshot(1,pixels));state.apply([64,0,1,2]);state.apply([-65,0,1,2]);
    assert.equal(state.pixels.size,32768);
});

// Execute the production clients with a small DOM and deterministic clock.
// Tests drive their actual pointer/lifecycle handlers and inspect drawing calls.
function client(kind, responder) {
    let now=0,nextTimer=0,frames=[],requests=[];
    const timers=new Map();
    const setTimer=(fn,ms=0)=>{const id=++nextTimer;timers.set(id,{fn,at:now+ms});return id;};
    const clearTimer=id=>timers.delete(id);
    class Events {
        constructor(){this.listeners=new Map();}
        addEventListener(name,fn){const list=this.listeners.get(name)||[];list.push(fn);this.listeners.set(name,list);}
        dispatch(name,fields={}){for(const fn of this.listeners.get(name)||[])fn({type:name,preventDefault(){},...fields});}
    }
    class Element extends Events {
        constructor(id){super();this.id=id;this.hidden=false;this.children=[];this.attributes={};this.style={};this.value='1';this.classList={add(){},remove(){},toggle(){}};}
        setAttribute(k,v){this.attributes[k]=v;}
        getAttribute(k){return this.attributes[k];}
        appendChild(child){this.children.push(child);}
        replaceChildren(){this.children=[];}
        querySelectorAll(){return this.children;}
        querySelector(){return this.children.find(e=>e.attributes['aria-pressed']==='true');}
        closest(){return this.hidden?this:null;}
        getBoundingClientRect(){return {x:0,y:0,left:0,top:this.id==='pixelBottomControls'?730:0,right:390,bottom:844,width:390,height:kind==='mini'?844:480};}
        focus(){document.activeElement=this;}
        blur(){document.activeElement=null;}
        setPointerCapture(){}
        getContext(){return context2d;}
        get clientWidth(){return 390;}
        get clientHeight(){return 844;}
    }
    const context2d={clearRect(){frames=[];},fillRect(x,y,w,h){frames.push({x,y,w,h,color:this.fillStyle});},setTransform(){},save(){},restore(){},beginPath(){},rect(){},clip(){},moveTo(){},lineTo(){},stroke(){},strokeRect(){}};
    const html=fs.readFileSync(path.join(__dirname,kind==='mini'?'../templates/index.html':'../templates/admin/canvas.html'),'utf8');
    const elements=new Map(Array.from(html.matchAll(/id="([^"]+)"/g),m=>[m[1],new Element(m[1])]));
    for(const id of ['pixelAdminTools','pixelColorPicker','pixelTools','pixelBackground','canvasArchive','canvasBanner'])if(elements.has(id))elements.get(id).hidden=true;
    for(const canvas of ['pixelCanvas','pixelMagnifier','adminCanvas'])if(elements.has(canvas))elements.get(canvas).parentElement=new Element('stage');
    const document=new Events();document.hidden=false;document.getElementById=id=>elements.get(id)||null;
    document.createElement=()=>new Element('created');document.body=new Element('body');document.documentElement=new Element('html');
    const window=new Events();const navigator={onLine:true};
    Object.assign(window,{document,navigator,AbortController,URLSearchParams,Promise,Map,Number,Date,console,
        setTimeout:setTimer,clearTimeout:clearTimer,requestAnimationFrame:fn=>setTimer(()=>fn(now),16),cancelAnimationFrame:clearTimer,
        performance:{now:()=>now},devicePixelRatio:1,localStorage:{getItem(){return null;},setItem(){}},scrollY:0,scrollTo(){},
        ResizeObserver:class{constructor(fn){this.fn=fn;}observe(){this.fn();}},
        kkepikApp:{ready:Promise.resolve(),lowData:false},
        Telegram:{WebApp:{initData:'test',themeParams:{bg_color:'#fff',button_color:'#2481cc'},colorScheme:'light',isVersionAtLeast(){return true;},expand(){},requestFullscreen(){},exitFullscreen(){},disableVerticalSwipes(){},enableVerticalSwipes(){},onEvent(){},HapticFeedback:{impactOccurred(){}},BackButton:{onClick(){},offClick(){},show(){},hide(){}}}},
        fetch:async(url,options={})=>{
            if(url==='/admin/canvas/token')return {ok:true,json:async()=>({success:true,token:'test-token'})};
            assert.match(url,/\/state\?|\/place$/);const record={url,signal:options.signal,at:now,body:options.body?JSON.parse(options.body):null};requests.push(record);
            return {ok:true,json:async()=>responder(record,requests.length)};
        }
    });window.window=window;
    const sandbox=vm.createContext(window);
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/js/pixel_state.js'),'utf8'),sandbox);
    vm.runInContext(fs.readFileSync(path.join(__dirname,`../static/js/pixel_${kind==='mini'?'canvas':'admin'}.js`),'utf8'),sandbox);
    async function tick(ms){const until=now+ms;await flush();let count=0;while(true){const due=[...timers].filter(([,t])=>t.at<=until).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;assert(++count<10000,'timer loop');now=due[1].at;timers.delete(due[0]);due[1].fn();await flush();}now=until;await flush();}
    return {window,document,navigator,elements,requests,tick,frames:()=>frames,click:id=>elements.get(id).dispatch('click'),pointer:(name,x,y)=>elements.get(kind==='mini'?'pixelCanvas':'adminCanvas').dispatch(name,{clientX:x,clientY:y,pointerId:1,pointerType:'touch',button:0})};
}

test('mini: archive stays hidden and makes no canvas reads until opened, then stops on exit',async()=>{
    const page=client('mini',()=>snapshot(1,[[0,0,5,1]]));await page.tick(10000);
    assert.equal(page.requests.length,0);assert.equal(page.elements.get('pixelBackground').hidden,true);
    assert.equal(page.elements.get('canvasArchive').hidden,false);assert.equal(page.elements.get('canvasBanner').hidden,false);
    page.click('canvasBanner');await page.tick(30);
    assert.equal(page.elements.get('pixelBackground').hidden,false);assert.equal(page.requests.length,1);
    page.click('pixelDone');assert.equal(page.elements.get('pixelBackground').hidden,true);
    const before=page.requests.length;page.window.dispatch('online');page.window.dispatch('pageshow',{persisted:true});await page.tick(10000);
    assert.equal(page.requests.length,before);assert.equal(page.elements.get('main-content').inert,false);
    page.click('canvasBanner');await page.tick(30);assert.equal(page.requests.length,before+1);
    page.window.dispatch('pagehide');
});

for(const kind of ['mini','admin'])test(`${kind}: distant artwork is drawn during drag before releasing the pointer`,async()=>{
    const page=client(kind,()=>snapshot(1,[[30,0,5,1]]));await page.tick(500);
    if(kind==='mini')page.click('canvasBanner');else page.click('adminPan');await page.tick(30);
    const before=page.requests.length,cell=kind==='mini'?24:16;
    page.pointer('pointerdown',200,200);page.pointer('pointermove',200-30*cell,200);await page.tick(30);
    assert(page.frames().some(p=>p.color===colors[5] && p.x>=0 && p.x<390),'off-screen cached artwork appears before pointerup');
    assert.equal(page.requests.length,before,'camera movement does not request snapshots');
    for(const request of page.requests){const params=new URL(request.url,'http://local').searchParams;assert.equal(params.get('x0'),'-64');assert.equal(params.get('y1'),'128');assert.equal(request.signal.aborted,false);}
    page.window.dispatch('pagehide');
});

test('mini: other players keep updating while pointer movement continues',async()=>{
    const page=client('mini',(_,index)=>snapshot(index,[[0,0,index===1?2:5,index]],index===1));await page.tick(500);page.click('canvasBanner');await page.tick(30);
    page.pointer('pointerdown',200,200);
    for(let i=0;i<60;i++){page.pointer('pointermove',190-i*.2,200);await page.tick(40);}
    assert(page.requests.length>=3 && page.requests.length<=5,'polling continues without one request per pointermove');
    assert(page.frames().some(p=>p.color===colors[5]));
    assert(page.requests.every(r=>!r.signal.aborted));page.window.dispatch('pagehide');
});

for(const event of ['offline','pagehide','visibilitychange'])test(`mini: late response after ${event} cannot overwrite resumed state`,async()=>{
    const gate=deferred();const page=client('mini',(_,index)=>index===1?gate.promise:snapshot(2,[[0,0,5,2]]));await page.tick(500);page.click('canvasBanner');await page.tick(10);
    if(event==='visibilitychange'){page.document.hidden=true;page.document.dispatch(event);page.document.hidden=false;page.document.dispatch(event);}
    else if(event==='offline'){page.navigator.onLine=false;page.window.dispatch(event);page.navigator.onLine=true;page.window.dispatch('online');}
    else{page.window.dispatch(event);page.window.dispatch('pageshow',{persisted:true});}
    await page.tick(30);gate.resolve(snapshot(1,[[0,0,2,1]]));await page.tick(30);
    assert(page.frames().some(p=>p.color===colors[5]));assert(!page.frames().some(p=>p.color===colors[2]));
    assert.equal(page.requests[0].signal.aborted,true);page.window.dispatch('pagehide');
});

test('mini: expired event disables drawing but keeps canvas navigation and admin controls',async()=>{
    const page=client('mini',record=>({...snapshot(1),admin:true,event:{ends_at_ms:1500,server_time_ms:500+record.at,finished:false}}));
    await page.tick(500);page.click('canvasBanner');assert.equal(page.elements.get('pixelColorToggle').disabled,false);
    await page.tick(600);assert.equal(page.elements.get('pixelColorToggle').disabled,true);
    assert.equal(page.elements.get('pixelEventDeadline').textContent,'Игра завершена');
    page.pointer('pointerdown',150,180);page.pointer('pointerup',150,180);await page.tick(20);
    assert.equal(page.elements.get('pixelBanScreen').hidden,true);
    page.click('pixelAdminToggle');assert.equal(page.elements.get('pixelColorToggle').disabled,false);
    page.click('pixelDone');assert.equal(page.elements.get('main-content').inert,false);page.window.dispatch('pagehide');
});

test('mini: an older access snapshot in the same millisecond cannot clear a newer ban',async()=>{
    const page=client('mini',(_,index)=>({...snapshot(1),banned:index===1,access_at_ms:1000,access_revision:index===1?2:1}));
    await page.tick(500);page.click('canvasBanner');await page.tick(20);assert.equal(page.elements.get('pixelBanScreen').hidden,false);
    await page.tick(1600);assert(page.requests.length>1);assert.equal(page.elements.get('pixelBanScreen').hidden,false);
    page.click('pixelBanBack');assert.equal(page.elements.get('main-content').inert,false);assert.equal(page.elements.get('pixelTools').hidden,true);
    page.window.dispatch('pagehide');
});

test('mini: 250 ms placement survives an older read with an obsolete one-second cooldown',async()=>{
    let confirmed=false;
    const page=client('mini',record=>{
        if(record.url.endsWith('/place')){
            confirmed=true;return {success:true,pixel:[record.body.x,record.body.y,record.body.color,10],remaining_ms:250,player_revision:10,cooldown_ms:250,cooldown_at_ms:2000};
        }
        return {...snapshot(9),player_revision:9,remaining_ms:confirmed?1000:0,cooldown_ms:confirmed?1000:250,cooldown_at_ms:1000};
    });
    await page.tick(500);page.click('canvasBanner');await page.tick(20);
    page.pointer('pointerdown',150,180);page.pointer('pointerup',150,180);await page.tick(30);
    const writes=()=>page.requests.filter(r=>r.url.endsWith('/place'));
    assert.equal(writes().length,1);assert.match(page.elements.get('pixelHint').textContent,/0,25/);
    await page.tick(100);assert.match(page.elements.get('pixelHint').textContent,/0,25/);
    page.pointer('pointerdown',174,180);page.pointer('pointerup',174,180);await page.tick(5);
    assert.equal(writes().length,1,'cannot paint before the server slot ends');
    await page.tick(150);page.pointer('pointerdown',174,180);page.pointer('pointerup',174,180);await page.tick(5);
    assert.equal(writes().length,2,'older state did not extend a newer acknowledged slot');page.window.dispatch('pagehide');
});
