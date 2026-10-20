(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const canvas = $('adminCanvas'), ctx = canvas.getContext('2d');
    const board = {x0:-64,y0:-128,x1:64,y1:128};
    const state = new window.KepikPixelState(board);
    const livePixels = state.pixels, framePixels = new Map(), pointers = new Map();
    const camera = { x: 0, y: 0, cell: 16 };
    let fitView = false;
    const names = ['Белый','Светло-серый','Серый','Чёрный','Розовый','Красный','Оранжевый','Коричневый','Жёлтый','Салатовый','Зелёный','Бирюзовый','Голубой','Синий','Сиреневый','Фиолетовый'];
    let palette = [], selectedColor = 0, selection = null, mode = 'select';
    let width = 0, height = 0, gesture = null, pinch = null, token = '', tokenAt = 0;
    let timer = null, pendingFill = false, epoch = 0, failures = 0, pageActive = true;
    let tokenRequest = null;
    let historyMode = false, historyArea = null, history = [], frame = 0, playing = false;
    let inspectedCell=null, inspectorEpoch=0, inspectedAuthor=null, banPending=false, bansEpoch=0;
    let rollbackPreview=null, rollbackEpoch=0, rollbackPending=false;
    let baselineCount = 0, historyStartedAt = 0;
    let playAt = 0, playRemainder = 0, animation = null;
    let playbackSpeed = 60;
    const status = text => { $('adminStatus').textContent = text; };
    const cellAt = p => ({ x: camera.x+(p.x-width/2)/camera.cell, y: camera.y+(p.y-height/2)/camera.cell });
    const point = e => { const r=canvas.getBoundingClientRect(); return {x:e.clientX-r.left,y:e.clientY-r.top}; };
    const viewArea = () => ({x0:Math.max(board.x0,Math.floor(camera.x-width/2/camera.cell)-1),y0:Math.max(board.y0,Math.floor(camera.y-height/2/camera.cell)-1),x1:Math.min(board.x1,Math.ceil(camera.x+width/2/camera.cell)+1),y1:Math.min(board.y1,Math.ceil(camera.y+height/2/camera.cell)+1)});
    const onBoard=c=>c.x>=board.x0 && c.x<board.x1 && c.y>=board.y0 && c.y<board.y1;
    function clampCamera(){
        const pad=18/camera.cell,halfX=width/2/camera.cell,halfY=height/2/camera.cell;
        camera.x=halfX-pad>=(board.x1-board.x0)/2 ? (board.x0+board.x1)/2
            : Math.max(board.x0+halfX-pad,Math.min(board.x1-halfX+pad,camera.x));
        camera.y=halfY-pad>=(board.y1-board.y0)/2 ? (board.y0+board.y1)/2
            : Math.max(board.y0+halfY-pad,Math.min(board.y1-halfY+pad,camera.y));
    }
    // All pixels are already cached; the viewport can safely show the whole board.
    const minimumCell=()=>Math.min(64,Math.max(1,width-36)/(board.x1-board.x0),Math.max(1,height-36)/(board.y1-board.y0));

    async function getToken() {
        if (token && Date.now()-tokenAt < 8*60*1000) return token;
        if (!tokenRequest) {
            tokenRequest = (async () => {
                const signal = new AbortController();
                const timeout = setTimeout(() => signal.abort(), 6000);
                try {
                    const r=await fetch('/admin/canvas/token',{cache:'no-store',redirect:'error',signal:signal.signal});
                    if (!r.ok) throw new Error('Войдите в админку заново.');
                    const data=await r.json();
                    if (!data.success || !data.token) throw new Error('Войдите в админку заново.');
                    token=data.token; tokenAt=Date.now(); return token;
                } finally { clearTimeout(timeout); }
            })().finally(() => { tokenRequest = null; });
        }
        return tokenRequest;
    }
    async function api(path, options={}) {
        const headers={'X-Canvas-Admin-Token':await getToken(),...(options.headers || {})};
        if (options.signal && options.signal.aborted) throw new Error('Запрос отменён.');
        const ownController = options.signal ? null : new AbortController();
        const timeout = ownController ? setTimeout(()=>ownController.abort(),10000) : null;
        try {
            const response=await fetch('/api/pixels/admin/'+path,{...options,signal:options.signal || ownController.signal,headers,cache:'no-store'});
            const data=await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Полотно недоступно.');
            return data;
        } finally { clearTimeout(timeout); }
    }
    function setupPalette(colors) {
        if (palette.length) return;
        palette=colors;
        colors.forEach((color,i) => {
            const b=document.createElement('button'); b.type='button'; b.setAttribute('aria-label',names[i]);
            b.setAttribute('aria-pressed',String(i===selectedColor));
            const s=document.createElement('span'); s.style.backgroundColor=color; b.appendChild(s);
            b.addEventListener('click',() => {
                selectedColor=i;
                $('adminPalette').querySelectorAll('button').forEach((button,j) => button.setAttribute('aria-pressed',String(i===j)));
            }); $('adminPalette').appendChild(b);
        });
    }
    function draw() {
        ctx.clearRect(0,0,width,height);
        const left=camera.x-width/2/camera.cell, top=camera.y-height/2/camera.cell;
        const boardX=(board.x0-left)*camera.cell,boardY=(board.y0-top)*camera.cell;
        const boardW=(board.x1-board.x0)*camera.cell,boardH=(board.y1-board.y0)*camera.cell;
        ctx.fillStyle='#343b3e12';ctx.fillRect(0,0,width,height);ctx.clearRect(boardX,boardY,boardW,boardH);
        ctx.save();ctx.beginPath();ctx.rect(boardX,boardY,boardW,boardH);ctx.clip();
        const data=historyMode?framePixels:livePixels;
        for (const pixel of state.visible(viewArea(), data)) {
            if (pixel[2]<0) continue;
            const x=Math.round((pixel[0]-left)*camera.cell), y=Math.round((pixel[1]-top)*camera.cell);
            ctx.fillStyle=palette[pixel[2]];
            ctx.fillRect(x,y,Math.round((pixel[0]+1-left)*camera.cell)-x,Math.round((pixel[1]+1-top)*camera.cell)-y);
        }
        if (camera.cell>=12 && !historyMode) {
            ctx.beginPath();
            for(let x=(Math.ceil(left)-left)*camera.cell;x<width;x+=camera.cell){ctx.moveTo(x,0);ctx.lineTo(x,height);}
            for(let y=(Math.ceil(top)-top)*camera.cell;y<height;y+=camera.cell){ctx.moveTo(0,y);ctx.lineTo(width,y);}
            ctx.strokeStyle='#343b3e18';ctx.lineWidth=.5;ctx.stroke();
        }
        ctx.restore();ctx.strokeStyle='#426e8b';ctx.lineWidth=2;ctx.strokeRect(boardX,boardY,boardW,boardH);
        if(inspectedCell && !historyMode)ctx.strokeRect((inspectedCell.x-left)*camera.cell,(inspectedCell.y-top)*camera.cell,camera.cell,camera.cell);
        const a=historyMode?historyArea:selection;
        if(a) {
            ctx.strokeStyle='#426e8b';ctx.lineWidth=2;
            if(!historyMode){ctx.fillStyle='#426e8b25';ctx.fillRect((a.x0-left)*camera.cell,(a.y0-top)*camera.cell,(a.x1-a.x0)*camera.cell,(a.y1-a.y0)*camera.cell);}
            ctx.strokeRect((a.x0-left)*camera.cell,(a.y0-top)*camera.cell,(a.x1-a.x0)*camera.cell,(a.y1-a.y0)*camera.cell);
        }
    }
    function resize() {
        const box=canvas.parentElement.getBoundingClientRect(); width=box.width;height=box.height;
        const dpr=Math.min(devicePixelRatio || 1,2);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
        ctx.setTransform(dpr,0,0,dpr,0,0);ctx.imageSmoothingEnabled=false;
        camera.cell=fitView?minimumCell():Math.max(camera.cell,minimumCell());clampCamera();
        draw();
    }
    function cancelRead() { epoch++; state.cancel(); clearTimeout(timer); timer=null; }
    function scheduleSync(delay) {
        clearTimeout(timer); timer=null;
        if (!historyMode && pageActive && !document.hidden && navigator.onLine) timer=setTimeout(sync,delay);
    }
    async function sync() {
        clearTimeout(timer);timer=null;
        if(historyMode || !pageActive || document.hidden || !navigator.onLine || state.busy)return;
        const ownEpoch=epoch;
        try {
            const data=await state.read((area,signal)=>api('state?'+new URLSearchParams(area),{signal}));
            if(!data || ownEpoch!==epoch || historyMode || !pageActive || document.hidden)return;
            setupPalette(data.palette);clampCamera();draw();failures=0;status('Полотно обновлено.');
            $('adminEventStatus').textContent=data.event ? (data.event.finished
                ? 'Ивент завершён. Просмотр и модерация доступны.'
                : 'Рисование открыто до '+new Date(data.event.ends_at_ms).toLocaleString('ru-RU')+'.') : '';
        }catch(e){if(ownEpoch===epoch && !historyMode && pageActive && !document.hidden){failures=Math.min(failures+1,5);status(e.message);}}
        finally{if(ownEpoch===epoch && !state.busy)scheduleSync(failures?Math.min(30000,1000*2**failures):2500);}
    }
    function updateSelection() {
        const count=selection?(selection.x1-selection.x0)*(selection.y1-selection.y0):0;
        $('adminSelection').textContent=selection?`${selection.x0}, ${selection.y0} → ${selection.x1-1}, ${selection.y1-1} · ${count.toLocaleString('ru-RU')} клеток`:'Проведите по полотну, чтобы выбрать область.';
        const disabled=!count || count>65536 || pendingFill || historyMode;
        $('adminFill').disabled=disabled;$('adminErase').disabled=disabled;
    }
    async function fill(color) {
        if(!selection || pendingFill || historyMode) return;
        pendingFill=true;updateSelection();status('Применяем…');
        const area={...selection};
        cancelRead();
        try {
            const result = await api('fill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...area,color})});
            for(let y=area.y0;y<area.y1;y++)for(let x=area.x0;x<area.x1;x++)state.apply([x,y,color,result.revision]);
            status(color===-1?'Область стёрта.':'Область закрашена.');
            selection=null;draw();scheduleSync(0);
        }catch(e){status(e.name==='TimeoutError'?'Проверьте полотно: запрос мог уже выполниться.':e.message);scheduleSync(0);}
        finally{pendingFill=false;updateSelection();}
    }
    $('adminFill').addEventListener('click',()=>fill(selectedColor));
    $('adminErase').addEventListener('click',()=>fill(-1));
    function setMode(value){
        mode=value;inspectorEpoch++;inspectedAuthor=null;$('adminBanAuthor').hidden=true;$('adminRollbackAuthor').hidden=true;
        ['adminSelect','adminPan','adminInspect'].forEach((id,i)=>$(id).setAttribute('aria-pressed',String(value===['select','pan','inspect'][i])));
        $('adminPixelInfo').hidden=value!=='inspect';
        if(value==='inspect'){selection=null;updateSelection();}else inspectedCell=null;
        draw();
    }
    function authorText(event){
        if(event.actor_kind==='web_admin')return 'Веб-администратор';
        if(event.actor_kind!=='telegram' || !event.actor_id)return event.source==='admin'?'Администратор · автор не записан':'Автор не записан';
        const name=event.actor_name || 'Пользователь Telegram';
        return name+(event.actor_username?' · @'+event.actor_username:'')+' · ID '+event.actor_id;
    }
    async function inspectPixel(cell){
        const x=Math.floor(cell.x),y=Math.floor(cell.y);
        if(!onBoard({x,y}))return;
        inspectedCell={x,y};draw();
        const own=++inspectorEpoch;
        inspectedAuthor=null;$('adminBanAuthor').hidden=true;$('adminRollbackAuthor').hidden=true;
        $('adminPixelTitle').textContent=`Клетка ${x}, ${y}`;
        $('adminPixelAuthor').textContent='Загружаем автора…';$('adminPixelEdits').replaceChildren();
        try{
            const data=await api('pixel?'+new URLSearchParams({x,y}));
            if(own!==inspectorEpoch || mode!=='inspect' || historyMode)return;
            const pixel=data.pixel;
            $('adminPixelAuthor').textContent=pixel
                ?(pixel.color<0?'Стерта · ':'Последняя правка · ')+authorText(pixel)+(pixel.placed_at_ms?' · '+new Date(pixel.placed_at_ms).toLocaleString('ru-RU'):'')
                :'Пустая клетка';
            if(pixel && pixel.actor_kind==='telegram' && pixel.actor_id){
                inspectedAuthor={id:pixel.actor_id,banned:data.author_banned};
                $('adminBanAuthor').hidden=pixel.actor_id===1084693264;$('adminBanAuthor').disabled=banPending;
                $('adminBanAuthor').textContent=data.author_banned?'Разрешить рисование':'Заблокировать рисование';
                $('adminRollbackAuthor').hidden=false;
            }
            data.edits.forEach(event=>{
                const li=document.createElement('li');
                li.textContent=(event.source==='baseline'?'Начальный снимок':event.source==='rollback'?'Откат':(event.color<0?'Стирание':'Закрашивание'))+' · '+authorText(event)+' · '+new Date(event.at_ms).toLocaleString('ru-RU');
                $('adminPixelEdits').appendChild(li);
            });
            status('Показаны последние 20 правок клетки.');
        }catch(error){if(own===inspectorEpoch)$('adminPixelAuthor').textContent=error.message;}
    }
    async function loadBans(){
        const own=++bansEpoch;
        $('adminBansList').textContent='Загружаем…';
        try{
            const data=await api('bans');
            if(own!==bansEpoch || !$('adminBansPanel').open)return;
            $('adminBansList').replaceChildren();
            if(!data.bans.length)$('adminBansList').textContent='Заблокированных нет.';
            for(const entry of data.bans){
                const row=document.createElement('div');row.className='canvas-ban-row';
                const label=document.createElement('span');label.textContent='ID '+entry.telegram_id;
                const time=document.createElement('small');time.textContent=new Date(entry.banned_at_ms).toLocaleString('ru-RU');label.appendChild(time);
                const button=document.createElement('button');button.type='button';button.textContent='Разбанить';
                button.addEventListener('click',async()=>{
                    if(button.disabled)return;button.disabled=true;
                    // Invalidate a list response taken before this mutation.
                    bansEpoch++;
                    try{
                        await api('ban',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({telegram_id:entry.telegram_id,banned:false})});
                        if(inspectedAuthor?.id===entry.telegram_id)inspectPixel(inspectedCell);
                        if($('adminBansPanel').open)loadBans();status('Рисование разрешено.');
                    }catch(error){status(error.message);button.disabled=false;}
                });
                const actions=document.createElement('div');actions.className='canvas-ban-actions';
                const undo=document.createElement('button');undo.type='button';undo.textContent='Откат';
                undo.addEventListener('click',()=>chooseRollback(entry.telegram_id));
                actions.appendChild(undo);actions.appendChild(button);
                row.appendChild(label);row.appendChild(actions);$('adminBansList').appendChild(row);
            }
        }catch(error){if(own===bansEpoch)$('adminBansList').textContent=error.message;}
    }
    $('adminBansPanel').addEventListener('toggle',()=>{
        if($('adminBansPanel').open)loadBans();else bansEpoch++;
    });
    $('adminBanAuthor').addEventListener('click',async()=>{
        if(!inspectedAuthor || banPending)return;
        const author=inspectedAuthor,own=inspectorEpoch;
        banPending=true;$('adminBanAuthor').disabled=true;bansEpoch++;
        try{
            const data=await api('ban',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({telegram_id:author.id,banned:!author.banned})});
            if(own===inspectorEpoch && inspectedAuthor===author){
                author.banned=data.banned;$('adminBanAuthor').textContent=data.banned?'Разрешить рисование':'Заблокировать рисование';
                status(data.banned?'Рисование заблокировано.':'Рисование разрешено.');
            }
            if($('adminBansPanel').open)loadBans();
        }catch(error){if(own===inspectorEpoch)status(error.message);}
        finally{banPending=false;$('adminBanAuthor').disabled=false;}
    });
    function resetRollback(){
        rollbackEpoch++;rollbackPreview=null;$('adminRollbackConfirm').hidden=true;
        $('adminRollbackInfo').textContent='';
    }
    function chooseRollback(id){
        if(rollbackPending)return;
        $('adminRollbackId').value=String(id);$('adminRollbackPanel').open=true;
        checkRollback();$('adminRollbackPanel').scrollIntoView({block:'nearest'});
    }
    async function checkRollback(){
        if(rollbackPending)return;
        resetRollback();const own=rollbackEpoch,id=Number($('adminRollbackId').value);
        if(!Number.isSafeInteger(id) || id<1){$('adminRollbackInfo').textContent='Укажите Telegram ID.';return;}
        $('adminRollbackInfo').textContent='Проверяем правки…';
        try{
            const data=await api('rollback?'+new URLSearchParams({telegram_id:id}));
            if(own!==rollbackEpoch)return;
            rollbackPreview={...data,request_id:window.crypto?.randomUUID?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)};
            $('adminRollbackInfo').textContent='ID '+id+' · правок: '+data.undone_events+' · клеток к восстановлению: '+data.changed_cells+'.';
            $('adminRollbackConfirm').hidden=!data.undone_events;
        }catch(error){if(own===rollbackEpoch)$('adminRollbackInfo').textContent=error.message;}
    }
    $('adminRollbackAuthor').addEventListener('click',()=>{if(inspectedAuthor)chooseRollback(inspectedAuthor.id);});
    $('adminRollbackId').addEventListener('input',resetRollback);
    $('adminRollbackForm').addEventListener('submit',e=>{e.preventDefault();checkRollback();});
    $('adminRollbackConfirm').addEventListener('click',async()=>{
        if(!rollbackPreview || rollbackPending)return;
        const preview=rollbackPreview,own=rollbackEpoch;
        rollbackPending=true;$('adminRollbackConfirm').disabled=true;$('adminRollbackCheck').disabled=true;
        try{
            const data=await api('rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
                telegram_id:preview.telegram_id,through_id:preview.through_id,request_id:preview.request_id,
            })});
            if(!historyMode){cancelRead();scheduleSync(0);}
            if(own===rollbackEpoch){
                rollbackPreview=null;$('adminRollbackConfirm').hidden=true;
                $('adminRollbackInfo').textContent='Правок отменено: '+data.undone_events+' · клеток восстановлено: '+data.changed_cells+'.';
            }
            if(inspectedCell && mode==='inspect' && !historyMode)inspectPixel(inspectedCell);
        }catch(error){if(own===rollbackEpoch)$('adminRollbackInfo').textContent=error.name==='AbortError'?'Проверьте полотно: откат мог выполниться. Повтор безопасен.':error.message;}
        finally{rollbackPending=false;$('adminRollbackConfirm').disabled=false;$('adminRollbackCheck').disabled=false;}
    });
    $('adminInspect').addEventListener('click',()=>setMode('inspect'));
    $('adminSelect').addEventListener('click',()=>setMode('select'));
    $('adminPan').addEventListener('click',()=>setMode('pan'));
    function changedView(){clampCamera();draw();}
    function zoom(factor){camera.cell=Math.max(minimumCell(),Math.min(64,camera.cell*factor));fitView=camera.cell===minimumCell();changedView();}
    $('adminZoomIn').addEventListener('click',()=>zoom(1.4));$('adminZoomOut').addEventListener('click',()=>zoom(1/1.4));
    $('adminFit')?.addEventListener('click',()=>{fitView=true;camera.cell=minimumCell();camera.x=(board.x0+board.x1)/2;camera.y=(board.y0+board.y1)/2;changedView();});
    $('adminHome').addEventListener('click',()=>{if(historyMode)return;fitView=false;camera.x=0;camera.y=0;camera.cell=16;resize();});
    $('adminJump').addEventListener('submit',e=>{e.preventDefault();if(historyMode)return;camera.x=Number($('adminX').value);camera.y=Number($('adminY').value);changedView();});
    canvas.addEventListener('pointerdown',e=>{
        if(historyMode || pendingFill || (e.pointerType==='mouse' && e.button!==0))return;
        e.preventDefault();const p=point(e);if(mode==='select' && !onBoard(cellAt(p)))return;pointers.set(e.pointerId,p);canvas.setPointerCapture(e.pointerId);
        if(pointers.size===1){gesture={point:p,world:cellAt(p),x:camera.x,y:camera.y,blocked:false};}
        else{const pair=Array.from(pointers.values());const c={x:(pair[0].x+pair[1].x)/2,y:(pair[0].y+pair[1].y)/2};pinch={distance:Math.max(1,Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y)),cell:camera.cell,anchor:cellAt(c)};if(gesture)gesture.blocked=true;}
    });
    canvas.addEventListener('pointermove',e=>{
        if(!pointers.has(e.pointerId) || historyMode)return;
        const p=point(e);pointers.set(e.pointerId,p);
        if(pointers.size>=2 && pinch){
            const pair=Array.from(pointers.values()),c={x:(pair[0].x+pair[1].x)/2,y:(pair[0].y+pair[1].y)/2};
            camera.cell=Math.max(minimumCell(),Math.min(64,pinch.cell*Math.hypot(pair[0].x-pair[1].x,pair[0].y-pair[1].y)/pinch.distance));
            fitView=camera.cell===minimumCell();
            camera.x=pinch.anchor.x-(c.x-width/2)/camera.cell;camera.y=pinch.anchor.y-(c.y-height/2)/camera.cell;changedView();
        }else if(gesture){
            if(mode==='pan' || mode==='inspect' || gesture.blocked){camera.x=gesture.x-(p.x-gesture.point.x)/camera.cell;camera.y=gesture.y-(p.y-gesture.point.y)/camera.cell;changedView();}
            else{const a=gesture.world,raw=cellAt(p),b={x:Math.max(board.x0,Math.min(board.x1-1,raw.x)),y:Math.max(board.y0,Math.min(board.y1-1,raw.y))};selection={x0:Math.floor(Math.min(a.x,b.x)),y0:Math.floor(Math.min(a.y,b.y)),x1:Math.floor(Math.max(a.x,b.x))+1,y1:Math.floor(Math.max(a.y,b.y))+1};updateSelection();draw();}
        }
    });
    function endPointer(e){
        if(!pointers.has(e.pointerId))return;
        if(mode==='inspect' && gesture && !gesture.blocked && pointers.size===1 && e.type==='pointerup'){
            const p=point(e);if(Math.hypot(p.x-gesture.point.x,p.y-gesture.point.y)<8)inspectPixel(cellAt(p));
        }
        pointers.delete(e.pointerId);pinch=null;if(pointers.size){const p=Array.from(pointers.values())[0];gesture={point:p,x:camera.x,y:camera.y,blocked:true};}else gesture=null;}
    ['pointerup','pointercancel','lostpointercapture'].forEach(name=>canvas.addEventListener(name,endPointer));
    canvas.addEventListener('wheel',e=>{e.preventDefault();zoom(Math.exp(-e.deltaY*.002));},{passive:false});

    function applyEvent(event) {
        if(event.restored){event.restored.forEach(applyEvent);return;}
        const a=historyArea;
        for(let y=Math.max(a.y0,event.y0);y<Math.min(a.y1,event.y1);y++)for(let x=Math.max(a.x0,event.x0);x<Math.min(a.x1,event.x1);x++){
            const key=x+','+y;
            if(event.color<0)framePixels.delete(key);else framePixels.set(key,[x,y,event.color,event.revision]);
        }
    }
    function frameLabel(){
        $('adminTimeline').value=String(frame);
        const at=frame?history[frame-1].at_ms:historyStartedAt;
        $('adminFrame').textContent=`${(frame-baselineCount).toLocaleString('ru-RU')} / ${(history.length-baselineCount).toLocaleString('ru-RU')} правок`+(at?' · '+new Date(at).toLocaleString('ru-RU'):'')+(frame>baselineCount?' · '+authorText(history[frame-1]):' · Начальное состояние');
    }
    function seek(target){
        target=Math.max(baselineCount, Math.min(history.length,target));
        if(target<frame){frame=0;framePixels.clear();}
        while(frame<target)applyEvent(history[frame++]);
        frameLabel();draw();
    }
    function pause(){playing=false;cancelAnimationFrame(animation);animation=null;$('adminPlay').textContent='Смотреть';}
    function setPlaybackSpeed(value,fromSlider=false){
        if(!Number.isFinite(value))return;
        playbackSpeed=Math.max(1,Math.min(10000,Math.round(value)));
        const unit={one:'правка',few:'правки',many:'правок',other:'правки'}[new Intl.PluralRules('ru-RU').select(playbackSpeed)];
        const control=$('adminSpeed'), label=playbackSpeed.toLocaleString('ru-RU')+' '+unit+'/с';
        if(!fromSlider)control.value=String(control.type==='range'?Math.round(Math.log10(playbackSpeed)*100):playbackSpeed);
        control.setAttribute('aria-valuetext',label);
        if($('adminSpeedValue'))$('adminSpeedValue').textContent=label;
        $('adminSpeedPresets')?.querySelectorAll('button').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.speed)===playbackSpeed)));
        // Keep the frame already on screen; the new rate takes effect immediately.
        playAt=performance.now();playRemainder=0;
    }
    $('adminSpeed').addEventListener('input',()=>setPlaybackSpeed($('adminSpeed').type==='range'?10**(Number($('adminSpeed').value)/100):Number($('adminSpeed').value),true));
    $('adminSpeedPresets')?.querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>setPlaybackSpeed(Number(button.dataset.speed))));
    function animate(now){
        if(!playing)return;
        const elapsed=Math.min(250,now-playAt);playAt=now;
        playRemainder=Math.min(Math.max(1,playbackSpeed/4),playRemainder+elapsed*playbackSpeed/1000);
        const target=Math.min(history.length,frame+Math.floor(playRemainder));
        const startedAt=frame,deadline=performance.now()+8;
        while(frame<target){
            applyEvent(history[frame++]);
            if(performance.now()>=deadline)break;
        }
        playRemainder-=frame-startedAt;
        if(frame>startedAt){frameLabel();draw();}
        if(frame>=history.length){pause();return;}
        animation=requestAnimationFrame(animate);
    }
    $('adminPlay').addEventListener('click',()=>{
        if(playing){pause();return;}if(frame>=history.length)seek(0);
        playing=true;playAt=performance.now();playRemainder=0;$('adminPlay').textContent='Пауза';animation=requestAnimationFrame(animate);
    });
    $('adminTimeline').addEventListener('input',()=>{pause();seek(Number($('adminTimeline').value));});
    $('adminHistory').addEventListener('click',async()=>{
        if(historyMode || pendingFill)return;
        historyMode=true;historyArea=selection?{...selection}:viewArea();
        if((historyArea.x1-historyArea.x0)*(historyArea.y1-historyArea.y0)>65536){historyMode=false;status('Выберите участок не больше 65 536 клеток.');return;}
        cancelRead();
        $('adminModeration').hidden=true;$('adminPlayback').hidden=false;
        $('adminSelect').disabled=true;$('adminPan').disabled=true;$('adminInspect').disabled=true;$('adminHome').disabled=true;
        $('adminPixelInfo').hidden=true;inspectorEpoch++;
        history=[];frame=0;baselineCount=0;framePixels.clear();draw();status('Загружаем историю…');
        const ownEpoch=epoch;
        try{
            let cursor=0,more=true,head=null;
            while(more && historyMode && ownEpoch===epoch){
                const data=await api('history?'+new URLSearchParams({...historyArea,after:cursor,limit:2000,...(head===null?{}:{until:head})}),{});
                if(!historyMode || ownEpoch!==epoch)return;
                setupPalette(data.palette);history.push(...data.events);cursor=data.cursor;more=data.more;head=data.head;historyStartedAt=data.started_at_ms;
                $('adminFrame').textContent=`Загружено ${history.length.toLocaleString('ru-RU')} правок…`;
                // Yield between pages so navigation remains usable.
                await new Promise(resolve=>setTimeout(resolve,0));
            }
            if(!historyMode || ownEpoch!==epoch)return;
            // One rollback is one instant frame, even when its restored cells
            // were fetched across several history pages.
            const grouped=[];
            for(const event of history){
                const last=grouped[grouped.length-1];
                if(event.source==='rollback' && last?.source==='rollback' && last.revision===event.revision)last.restored.push(event);
                else grouped.push(event.source==='rollback'?{...event,restored:[event]}:event);
            }
            history=grouped;
            while (baselineCount<history.length && history[baselineCount].source==='baseline') baselineCount++;
            const hasEdits=history.length>baselineCount;
            $('adminTimeline').min=String(baselineCount);$('adminTimeline').max=String(history.length);
            $('adminTimeline').disabled=!hasEdits;$('adminPlay').disabled=!hasEdits;
            seek(0);status(hasEdits?'История готова. Нажмите «Смотреть».':'В этом участке пока нет новых правок.');
        }catch(e){if(historyMode && ownEpoch===epoch)status(e.message);}
    });
    $('adminLive').addEventListener('click',()=>{
        pause();historyMode=false;epoch++;history=[];framePixels.clear();
        $('adminPlayback').hidden=true;$('adminModeration').hidden=false;
        $('adminPlay').disabled=true;$('adminTimeline').disabled=true;
        $('adminSelect').disabled=false;$('adminPan').disabled=false;$('adminInspect').disabled=false;$('adminHome').disabled=false;
        $('adminPixelInfo').hidden=mode!=='inspect';
        updateSelection();draw();scheduleSync(0);
    });
    new ResizeObserver(resize).observe(canvas.parentElement);
    document.addEventListener('visibilitychange',()=>{if(document.hidden){pause();cancelRead();pointers.clear();gesture=null;pinch=null;}else scheduleSync(0);});
    window.addEventListener('pagehide',()=>{pageActive=false;pause();cancelRead();pointers.clear();gesture=null;pinch=null;});
    window.addEventListener('pageshow',()=>{pageActive=true;scheduleSync(0);});
    window.addEventListener('offline',()=>{cancelRead();status('Нет связи с полотном.');});
    window.addEventListener('online',()=>scheduleSync(0));
    resize();scheduleSync(100);
})();
