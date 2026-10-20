(function () {
    'use strict';
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.initData || window.KepikPresence) return;

    const session = window.crypto && crypto.randomUUID ? crypto.randomUUID()
        : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    const state = { count: null, observedAt: 0 };
    window.KepikPresence = state;
    let sequence = 0, generation = 0, timer = null, controller = null;
    let pageActive = true, telegramActive = true, registered = false, failures = 0;

    function visible() {
        return pageActive && telegramActive && !document.hidden && navigator.onLine;
    }
    function publish(count, observedAt) {
        if (count !== null && (!Number.isSafeInteger(count) || count < 0
            || !Number.isSafeInteger(observedAt) || observedAt < state.observedAt)) return;
        state.count = count;
        if (count !== null) state.observedAt = observedAt;
        window.dispatchEvent(new CustomEvent('kkepik:presence', { detail: { ...state } }));
    }
    function schedule(delay) {
        clearTimeout(timer);timer=null;
        if (visible()) timer=setTimeout(heartbeat,delay);
    }
    async function heartbeat() {
        timer=null;
        if (!visible() || controller) return;
        const ownGeneration=generation, ownController=new AbortController();
        controller=ownController;registered=true;
        const timeout=setTimeout(()=>ownController.abort(),6000);
        try {
            const response=await fetch('/api/pixels/presence', {
                method:'POST',headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg.initData},
                body:JSON.stringify({session_id:session,sequence:++sequence,active:true}),
                signal:ownController.signal,cache:'no-store',
            });
            const data=await response.json();
            if(ownGeneration!==generation || ownController.signal.aborted || !visible())return;
            if(!response.ok || !data.success)throw new Error('Presence unavailable');
            publish(data.online_count,data.online_at_ms);failures=0;
        } catch(_error) {
            if(ownGeneration!==generation || !visible())return;
            failures=Math.min(failures+1,5);publish(null,0);
        } finally {
            clearTimeout(timeout);
            if(controller===ownController){
                controller=null;schedule(failures?Math.min(30000,1000*2**failures):15000);
            }
        }
    }
    function leave() {
        generation++;clearTimeout(timer);timer=null;
        if(controller){controller.abort();controller=null;}
        publish(null,0);
        if(!registered || !navigator.onLine)return;
        registered=false;
        // keepalive survives navigation; the sequence prevents an older
        // heartbeat arriving later from making a closed session active again.
        fetch('/api/pixels/presence',{
            method:'POST',keepalive:true,cache:'no-store',
            headers:{'Content-Type':'application/json','X-Telegram-Init-Data':tg.initData},
            body:JSON.stringify({session_id:session,sequence:++sequence,active:false}),
        }).catch(()=>{});
    }
    function refresh(){if(visible())schedule(0);else leave();}
    document.addEventListener('visibilitychange',refresh);
    window.addEventListener('offline',leave);
    window.addEventListener('online',refresh);
    window.addEventListener('pagehide',()=>{pageActive=false;leave();});
    window.addEventListener('pageshow',()=>{pageActive=true;refresh();});
    try {
        tg.onEvent('deactivated',()=>{telegramActive=false;leave();});
        tg.onEvent('activated',()=>{telegramActive=true;refresh();});
    } catch(_error){/* Older clients still provide document visibility. */}
    schedule(0);
}());
