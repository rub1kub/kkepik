(function () {
    'use strict';
    const $=id=>document.getElementById(id), panel=$('canvasChatPanel');
    if(!panel)return;
    const web=$('adminChatPanel'), tg=window.Telegram?.WebApp, toggle=$('pixelChatToggle');
    if(!web && !tg?.initData)return;
    const log=$('canvasChatMessages'), input=$('canvasChatInput'), send=$('canvasChatSend');
    const badge=$('canvasChatUnread'), status=$('canvasChatStatus'), bans=$('canvasChatBans');
    const storageKey='kkepik:canvas:chat:seen:v1:'+String(tg?.initDataUnsafe?.user?.id || 'local');
    let opened=false, active=false, blocked=false, admin=false, finished=false;
    let seen=null, head=0, revision=-1, observedAt=0, renderedRevision=-1, renderedHead=0, latestUnread=0, sendNotice='';
    let timer=null, readController=null, readEpoch=0, sending=false, draft=null, readyAt=0, readyTimer=null;
    let pageActive=true, failures=0, token='',tokenAt=0,tokenRequest=null,bansBusy=false;
    try{const saved=localStorage.getItem(storageKey);if(saved!==null && Number.isSafeInteger(Number(saved)) && Number(saved)>=0)seen=Number(saved);}catch(_error){}
    function remember(){try{localStorage.setItem(storageKey,String(seen));}catch(_error){}}
    const atBottom=()=>log.scrollHeight-log.scrollTop-log.clientHeight<45;
    function markSeen(value){seen=Math.max(seen??0,value);remember();updateBadge(head<=seen?0:Math.max(1,latestUnread));}
    function updateBadge(count){
        if(!badge)return;
        badge.hidden=count===0;badge.textContent=count>=100?'99+':String(count);
        toggle.setAttribute('aria-label',count?'Чат полотна, новых сообщений: '+(count>=100?'больше 99':count):'Чат полотна');
    }
    function updateSend(){
        clearTimeout(readyTimer);readyTimer=null;
        if(!input)return;
        const remaining=Math.max(0,readyAt-performance.now());
        input.disabled=blocked || finished;
        send.disabled=input.disabled || sending || remaining>0 || !input.value.trim() || !navigator.onLine;
        send.setAttribute('aria-label',remaining?'Отправить через '+Math.ceil(remaining/1000)+' с':'Отправить сообщение');
        if(blocked)status.textContent='Писать в чат тебе запрещено. Сообщения можно читать.';
        else if(finished)status.textContent='Ивент завершён. Чат доступен для чтения.';
        else if(sendNotice)status.textContent=sendNotice;
        else if(remaining && !sending && input.value.trim())status.textContent='Следующее сообщение через '+Math.ceil(remaining/1000)+' с';
        else if(!remaining && status.textContent.startsWith('Следующее сообщение через '))status.textContent='';
        if(remaining && opened && pageActive && !document.hidden)readyTimer=setTimeout(updateSend,Math.min(1000,remaining));
    }
    function updateState(data){
        if(!data || !Number.isSafeInteger(data.head) || !Number.isSafeInteger(data.revision) || !Number.isSafeInteger(data.observed_at_ms))return;
        if(data.revision<revision || (data.revision===revision && data.observed_at_ms<observedAt))return;
        const changed=data.revision>revision;
        head=data.head;revision=data.revision;observedAt=data.observed_at_ms;blocked=data.banned===true;latestUnread=Math.max(0,Math.min(100,data.unread||0));
        if(seen===null || seen>head){seen=head;remember();}
        updateBadge(head<=seen?0:Math.max(0,Math.min(100,data.unread||0)));
        if(Number.isFinite(data.remaining_ms))readyAt=performance.now()+Math.max(0,data.remaining_ms);
        updateSend();
        if(opened && changed && !readController)schedule(0);
    }
    async function headers(){
        if(!web)return {'X-Telegram-Init-Data':tg.initData};
        if(token && Date.now()-tokenAt<8*60*1000)return {'X-Canvas-Admin-Token':token};
        if(!tokenRequest){
            tokenRequest=(async()=>{
                const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),6000);
                try{
                    const r=await fetch('/admin/canvas/token',{cache:'no-store',redirect:'error',signal:controller.signal});
                    const d=await r.json();if(!r.ok || !d.token)throw new Error('Войдите в админку заново.');
                    token=d.token;tokenAt=Date.now();
                }finally{clearTimeout(timeout);}
            })().finally(()=>{tokenRequest=null;});
        }
        await tokenRequest;return {'X-Canvas-Admin-Token':token};
    }
    async function api(path,body,signal){
        const own=signal?null:new AbortController(),timeout=own?setTimeout(()=>own.abort(),8000):null;
        try{
            const h=await headers();
            if((signal||own.signal).aborted)throw new Error('Запрос отменён.');
            const r=await fetch('/api/pixels/'+path,{method:body?'POST':'GET',headers:{...h,...(body?{'Content-Type':'application/json'}:{})},
                ...(body?{body:JSON.stringify(body)}:{}),signal:signal||own.signal,cache:'no-store'});
            const data=await r.json();
            if(!r.ok || !data.success){const error=new Error(data.error||'Чат пока недоступен.');error.data=data;throw error;}
            return data;
        }finally{clearTimeout(timeout);}
    }
    function schedule(delay){
        clearTimeout(timer);timer=null;
        if(opened && pageActive && !document.hidden && navigator.onLine)timer=setTimeout(read,delay);
    }
    function cancelRead(){readEpoch++;clearTimeout(timer);timer=null;readController?.abort();readController=null;}
    async function moderate(path,body){
        try{await api('admin/chat/'+path,body);renderedRevision=-1;schedule(0);if(bans.open)loadBans();}
        catch(error){status.textContent=error.message;}
    }
    function render(messages,meta){
        const bottom=atBottom() || renderedRevision<0, previousHeight=log.scrollHeight,previousTop=log.scrollTop;
        log.replaceChildren();
        if(!messages.length){const empty=document.createElement('p');empty.className='canvas-chat-empty';empty.textContent='Пока тихо. Можно договориться о рисунке.';log.appendChild(empty);}
        for(const message of messages){
            const item=document.createElement('article');item.className='canvas-chat-message'+(message.own?' canvas-chat-message-own':'');item.dataset.messageId=String(message.id);
            const heading=document.createElement('div');heading.className='canvas-chat-message-meta';
            const name=document.createElement('strong');name.textContent=message.name;heading.appendChild(name);
            if(message.admin){const tag=document.createElement('span');tag.textContent='Админ';heading.appendChild(tag);}
            const time=document.createElement('time');time.dateTime=new Date(message.at_ms).toISOString();time.textContent=new Date(message.at_ms).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});heading.appendChild(time);
            const text=document.createElement('p');text.className='canvas-chat-message-text';text.textContent=message.text;item.append(heading,text);
            if(admin){
                const actions=document.createElement('div');actions.className='canvas-chat-actions';
                const hide=document.createElement('button');hide.type='button';hide.textContent='Скрыть';hide.addEventListener('click',()=>{hide.disabled=true;moderate('hide',{message_id:message.id}).finally(()=>{hide.disabled=false;});});actions.appendChild(hide);
                if(message.actor_id && !message.admin){
                    const ban=document.createElement('button');ban.type='button';ban.textContent='Бан чата';ban.addEventListener('click',()=>{ban.disabled=true;moderate('ban',{telegram_id:message.actor_id,banned:true}).finally(()=>{ban.disabled=false;});});actions.appendChild(ban);
                }
                item.appendChild(actions);
            }
            log.appendChild(item);
        }
        renderedRevision=meta.revision;renderedHead=meta.head;
        log.scrollTop=bottom?log.scrollHeight:Math.max(0,previousTop+log.scrollHeight-previousHeight);
        if(bottom)markSeen(meta.head);
    }
    async function read(){
        clearTimeout(timer);timer=null;
        if(!opened || !pageActive || document.hidden || !navigator.onLine || readController)return;
        const controller=new AbortController(),epoch=readEpoch;readController=controller;
        const timeout=setTimeout(()=>controller.abort(),8000);
        try{
            const data=await api((web?'admin/':'')+'chat/messages?after='+(seen??-1),null,controller.signal);
            if(epoch!==readEpoch || !opened || !pageActive || document.hidden || controller.signal.aborted)return;
            if(data.chat.revision<revision || (data.chat.revision===revision && data.chat.observed_at_ms<observedAt)){schedule(0);return;}
            admin=data.admin===true;bans.hidden=!admin;
            finished=!!(data.event && !data.event.active);
            updateState(data.chat);
            if(renderedRevision!==data.chat.revision)render(data.messages,data.chat);
            failures=0;status.textContent='';updateSend();
        }catch(error){if(epoch===readEpoch && opened && pageActive && !document.hidden){failures=Math.min(failures+1,5);status.textContent=error.name==='AbortError'?'Не удалось загрузить чат. Повторим.':error.message;}}
        finally{clearTimeout(timeout);if(epoch===readEpoch){readController=null;schedule(failures?Math.min(30000,1000*2**failures):5000);}}
    }
    async function loadBans(){
        if(!admin || !bans.open || bansBusy || !opened)return;
        bansBusy=true;
        try{
            const data=await api('admin/chat/bans');if(!opened)return;
            const list=$('canvasChatBansList');list.replaceChildren();
            if(!data.bans.length)list.textContent='Банов нет.';
            for(const row of data.bans){
                const item=document.createElement('div');item.className='canvas-chat-ban-row';
                const name=document.createElement('span');name.textContent='ID '+row.telegram_id;
                const button=document.createElement('button');button.type='button';button.textContent='Разбанить';
                button.addEventListener('click',()=>{button.disabled=true;moderate('ban',{telegram_id:row.telegram_id,banned:false}).finally(()=>{button.disabled=false;});});
                item.append(name,button);list.appendChild(item);
            }
        }catch(error){status.textContent=error.message;}finally{bansBusy=false;}
    }
    function keyboardLayout(){
        if(web || !opened)return;
        const viewport=window.visualViewport;
        const keyboard=viewport?Math.max(0,innerHeight-viewport.height-viewport.offsetTop):0;
        const available=viewport?.height || innerHeight;
        panel.style.bottom=keyboard>80?(keyboard+8)+'px':'';
        panel.style.height=keyboard>80?Math.max(160,Math.min(available-80,320))+'px':'';
    }
    function open(){
        if(opened || (!web && !active))return;
        opened=true;panel.hidden=false;renderedRevision=-1;
        if(!web){document.body.classList.add('pixel-chat-open');toggle.setAttribute('aria-expanded','true');window.KepikCanvasChat.onOpen?.();$('canvasChatClose').focus({preventScroll:true});}
        keyboardLayout();schedule(0);updateSend();
    }
    function close(){
        if(!opened)return;
        opened=false;cancelRead();clearTimeout(readyTimer);readyTimer=null;
        if(!web){panel.hidden=true;input.blur();document.body.classList.remove('pixel-chat-open');toggle.setAttribute('aria-expanded','false');window.KepikCanvasChat.onClose?.();if(active)toggle.focus({preventScroll:true});}
    }
    if(web){web.addEventListener('toggle',()=>web.open?open():close());}
    else{
        toggle.addEventListener('click',open);$('canvasChatClose').addEventListener('click',close);
        let swipe=null;
        $('canvasChatHandle').addEventListener('pointerdown',e=>{if(e.target.closest('button'))return;swipe={x:e.clientX,y:e.clientY};e.currentTarget.setPointerCapture(e.pointerId);});
        $('canvasChatHandle').addEventListener('pointerup',e=>{if(swipe && e.clientY-swipe.y>45 && Math.abs(e.clientX-swipe.x)<60)close();swipe=null;});
        $('canvasChatHandle').addEventListener('pointercancel',()=>{swipe=null;});
        input.addEventListener('input',()=>{sendNotice='';status.textContent='';updateSend();});
        $('canvasChatForm').addEventListener('submit',async event=>{
            event.preventDefault();updateSend();if(send.disabled)return;
            const text=input.value.trim();
            if(!draft || draft.text!==text)draft={text,request_id:window.crypto?.randomUUID?.() || Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)};
            const submitted=draft;sendNotice='';sending=true;updateSend();status.textContent='Отправляем…';
            try{
                const data=await api('chat/messages',submitted);
                if(input.value.trim()===submitted.text)input.value='';draft=null;updateState(data.chat);
                status.textContent='';renderedRevision=-1;schedule(0);
            }catch(error){if(error.data?.chat)updateState(error.data.chat);if(error.data?.event)finished=!error.data.event.active;
                sendNotice=error.name==='AbortError'?'Ответ не пришёл. Можно повторить — сообщение не продублируется.':error.message;
            }finally{sending=false;updateSend();}
        });
    }
    log.addEventListener('scroll',()=>{if(opened && atBottom())markSeen(renderedHead);},{passive:true});
    bans.addEventListener('toggle',()=>{if(bans.open)loadBans();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelRead();clearTimeout(readyTimer);}else{schedule(0);updateSend();}});
    window.addEventListener('pagehide',()=>{pageActive=false;cancelRead();clearTimeout(readyTimer);});
    window.addEventListener('pageshow',()=>{pageActive=true;schedule(0);updateSend();});
    window.addEventListener('offline',()=>{cancelRead();updateSend();if(opened)status.textContent='Нет связи. Сообщение останется в поле.';});
    window.addEventListener('online',()=>{schedule(0);updateSend();});
    window.visualViewport?.addEventListener('resize',keyboardLayout);
    window.visualViewport?.addEventListener('scroll',keyboardLayout);
    window.addEventListener('resize',keyboardLayout);
    window.KepikCanvasChat={
        query:()=>seen===null?{}:{chat_after:seen},updateState,open,close,
        isOpen:()=>opened,
        reset(){cancelRead();revision=-1;observedAt=0;head=0;seen=null;renderedRevision=-1;renderedHead=0;updateBadge(0);schedule(0);},
        setContext(value){active=value.active && !value.banned;admin=value.admin===true;finished=value.finished===true;bans.hidden=!admin;if(!active || value.banned)close();updateSend();},
        onOpen:null,onClose:null,
    };
})();
