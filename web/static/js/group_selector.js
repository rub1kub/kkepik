(function () {
    'use strict';
    const tg = window.Telegram?.WebApp;
    const tgWebAppData = window.tgWebAppData || tg?.initData || '';
    const $ = id => document.getElementById(id);
    const container = $('groupSelectorContainer'), currentName = $('currentGroupName');
    const toggle = $('groupSelectorToggle'), manage = $('groupSelectorManage');
    const arrow = $('groupSelectorArrow'), menu = $('groupSelectorDropdown');
    if (!container || !toggle || !manage || !menu) return;

    let favorites = [], userEntity = null, currentEntity = null, menuOpen = false;
    let catalog = null, catalogPromise = null, catalogFailed = false, searchVersion = 0;
    let sheet, input, results, confirmButton, error, status, retry, fieldLabel;
    let entityType = 'group', selected = null, activeOption = -1, saving = false, oldOverflow = '';
    const HISTORY_KEY = 'groupSelectorHistory';
    const paths = { plus: 'M12 5v14M5 12h14', close: 'm6 6 12 12M6 18 18 6',
        check: 'm5 12 4 4L19 6', trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7' };
    const key = entity => entity?.entity_type && entity.entity_id ? `${entity.entity_type}:${entity.entity_id}` : '';
    const same = (a, b) => Boolean(key(a) && key(a) === key(b));
    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }
    function icon(name) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}).forEach(([k,v]) => svg.setAttribute(k,v));
        const path = document.createElementNS(svg.namespaceURI, 'path');path.setAttribute('d',paths[name]);svg.append(path);return svg;
    }
    function button(text, handler, className) {
        const element = node('button',className,text);element.type='button';element.addEventListener('click',handler);return element;
    }
    function history() {
        try { const value=JSON.parse(localStorage.getItem(HISTORY_KEY)||'[]');return Array.isArray(value)?value.filter(e=>key(e)&&['group','teacher'].includes(e.entity_type)):[]; }
        catch (_) { return []; }
    }
    function saveHistory(items) { try {localStorage.setItem(HISTORY_KEY,JSON.stringify(items.slice(0,10)));}catch (_) {} }
    function remember(entity) {saveHistory([entity,...history().filter(e=>!same(e,entity))]);}
    function tracked() {
        const seen=new Set();return [userEntity,...favorites].filter(e=>{const id=key(e);if(!id||seen.has(id))return false;seen.add(id);return true;});
    }
    function renderEntitySwitcher() {
        currentName.textContent=currentEntity?.entity_name||currentEntity?.entity_id||'Моё расписание';
        const multiple=tracked().length>1;
        toggle.disabled=!multiple;arrow.toggleAttribute('hidden',!multiple);manage.hidden=false;manage.disabled=saving;
        toggle.setAttribute('aria-expanded',String(menuOpen));
    }
    function selectEntity(entity) {
        currentEntity=entity;remember(entity);renderEntitySwitcher();closeDropdown();
        tg?.HapticFeedback?.impactOccurred?.('soft');window.updateScheduleForEntity?.(entity);
    }
    function bootstrap(data) {
        if (Array.isArray(data?.favorites)) favorites=data.favorites.filter(e=>key(e));
        if (data?.user?.name_or_group) userEntity={entity_type:['teacher','Я преподаватель'].includes(data.user.role)?'teacher':'group',entity_id:data.user.name_or_group,entity_name:data.user.name_or_group};
        if (!currentEntity) currentEntity=userEntity||favorites[0]||null;
        renderEntitySwitcher();
    }
    document.addEventListener('kkepik:bootstrap-updated',event=>bootstrap(event.detail));
    async function init() {
        try {
            if (window.kkepikApp) bootstrap(await window.kkepikApp.ready);
            else if (tgWebAppData) {
                const [me,saved]=await Promise.all([
                    fetch('/api/me',{headers:{'X-Telegram-Init-Data':tgWebAppData}}).then(r=>r.json()),
                    fetch('/api/favorites?tgWebAppData='+encodeURIComponent(tgWebAppData)).then(r=>r.json())
                ]);bootstrap({user:me,favorites:saved.favorites});
            }
        } catch (_) {renderEntitySwitcher();}
        if (tgWebAppData) loadScheduleCatalog().catch(()=>{});
    }
    if (document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();

    function positionMenu() {
        const rect=container.getBoundingClientRect(), viewport=window.visualViewport;
        const width=viewport?.width||innerWidth, bottom=(viewport?.offsetTop||0)+(viewport?.height||innerHeight);
        const space=bottom-rect.bottom-16, above=space<160&&rect.top>space;
        menu.style.width=Math.min(rect.width,width-32)+'px';
        menu.style.left=Math.max(16,Math.min(rect.left,width-menu.getBoundingClientRect().width-16))+'px';
        menu.style.maxHeight=Math.max(80,Math.min(360,above?rect.top-24:space))+'px';
        menu.style.top=above?'auto':rect.bottom+6+'px';menu.style.bottom=above?innerHeight-rect.top+6+'px':'auto';
    }
    function closeDropdown(returnFocus=false) {
        menuOpen=false;menu.hidden=true;toggle.setAttribute('aria-expanded','false');
        if(returnFocus)toggle.focus({preventScroll:true});
    }
    function openDropdown(keyboard=false) {
        if(tracked().length<2)return;
        renderMenu();menuOpen=true;menu.hidden=false;toggle.setAttribute('aria-expanded','true');positionMenu();
        if(keyboard)menu.querySelector('[role="menuitem"]')?.focus({preventScroll:true});
    }
    function renderMenu() {
        menu.replaceChildren();const seen=new Set();
        function entry(entity, removable, note) {
            const id=key(entity);if(!id||seen.has(id))return;seen.add(id);
            const row=node('div','entity-menu-row');
            const choose=button('',()=>selectEntity(entity),'entity-menu-choice');choose.setAttribute('role','menuitem');
            const copy=node('span','entity-menu-copy');copy.append(node('span','entity-menu-name',entity.entity_name||entity.entity_id));
            if(note)copy.append(node('small','',note));choose.append(copy);
            if(same(entity,currentEntity)){choose.append(icon('check'));choose.setAttribute('aria-current','true');}
            row.append(choose);
            if(removable){const remove=button('',()=>removeFavorite(entity),'entity-menu-remove');remove.append(icon('trash'));remove.setAttribute('aria-label','Удалить '+(entity.entity_name||entity.entity_id)+' из избранного');row.append(remove);}
            menu.append(row);
        }
        if(userEntity)entry(userEntity,false,userEntity.entity_type==='group'?'Моя группа':'Моё расписание');
        const saved=favorites.filter(e=>!same(e,userEntity));
        if(saved.length)menu.append(node('p','entity-menu-heading','Сохранённые'));
        saved.forEach(e=>entry(e,true));
        const recent=history().filter(e=>!seen.has(key(e))).slice(0,5);
        if(recent.length)menu.append(node('p','entity-menu-heading','Недавние'));
        recent.forEach(e=>entry(e,false));
        const add=button('Добавить расписание',()=>{closeDropdown();openAddModal();},'entity-menu-add');add.prepend(icon('plus'));add.setAttribute('role','menuitem');menu.append(add);
    }
    toggle.addEventListener('click',()=>menuOpen?closeDropdown():openDropdown());
    toggle.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();openDropdown(true);}});
    manage.addEventListener('click',openAddModal);
    manage.addEventListener('pointerdown',()=>{if(!catalogFailed)loadScheduleCatalog().catch(()=>{});},{passive:true});
    document.addEventListener('pointerdown',event=>{if(menuOpen&&!container.contains(event.target)&&!menu.contains(event.target))closeDropdown();});
    document.addEventListener('keydown',event=>{if(menuOpen&&event.key==='Escape'){event.preventDefault();closeDropdown(true);}});
    menu.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();closeDropdown(true);return;}
        if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
        event.preventDefault();const items=[...menu.querySelectorAll('button:not(:disabled)')],index=items.indexOf(document.activeElement);
        const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;
        items[next]?.focus({preventScroll:true});
    });
    window.addEventListener('resize',()=>{if(menuOpen)positionMenu();resizeSheet();});
    window.visualViewport?.addEventListener('resize',resizeSheet);
    window.visualViewport?.addEventListener('scroll',resizeSheet);
    document.addEventListener('scroll',event=>{if(menuOpen&&!menu.contains(event.target))closeDropdown();},true);

    function showError(message) {
        if(sheet?.open){error.textContent=message;return;}
        let notice=$('entitySelectorStatus');
        if(!notice){notice=node('p','entity-selector-error');notice.id='entitySelectorStatus';notice.setAttribute('role','alert');container.after(notice);}
        notice.textContent=message;
    }
    async function mutateFavorite(action,entity) {
        const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),12000);
        try {
            const response=await fetch('/api/favorites/'+action,{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
                body:JSON.stringify({tgWebAppData,entity_type:entity.entity_type,entity_id:entity.entity_id,entity_name:entity.entity_name})});
            const data=await response.json();
            if(!response.ok||(!data.success&&!(action==='add'&&data.error==='Уже в избранном')))throw new Error(data.error||'Не удалось сохранить. Попробуйте ещё раз.');
        } finally {clearTimeout(timeout);}
    }
    async function addFavorite() {
        if(!selected||saving)return;
        const entity={...selected};saving=true;error.textContent='';setSaving();
        try {
            await mutateFavorite('add',entity);
            if(!favorites.some(e=>same(e,entity)))favorites.push(entity);
            closeAddModal();selectEntity(entity);
        } catch(e){showError(e.name==='AbortError'?'Не удалось сохранить. Попробуйте ещё раз.':e.message);}
        finally {saving=false;setSaving();renderEntitySwitcher();}
    }
    async function removeFavorite(entity) {
        if(saving||!window.confirm('Удалить '+(entity.entity_name||entity.entity_id)+' из избранного?'))return;
        saving=true;renderEntitySwitcher();
        try {
            await mutateFavorite('remove',entity);favorites=favorites.filter(e=>!same(e,entity));saveHistory(history().filter(e=>!same(e,entity)));
            if(same(currentEntity,entity)){const fallback=userEntity||favorites[0];if(fallback)selectEntity(fallback);else{currentEntity=null;closeDropdown();}}
            if(tracked().length<2)closeDropdown();else if(menuOpen){renderMenu();positionMenu();}
            renderEntitySwitcher();
        } catch(e){showError(e.name==='AbortError'?'Не удалось удалить. Попробуйте ещё раз.':e.message);}
        finally {saving=false;renderEntitySwitcher();}
    }
    function loadScheduleCatalog() {
        if(catalog)return Promise.resolve(catalog);
        if(!catalogPromise){
            catalogPromise=fetch('/api/schedule/catalog',{headers:{Accept:'application/json'}}).then(response=>{
                if(!response.ok)throw new Error('Не удалось загрузить список.');return response.json();
            }).then(data=>{
                if(!Array.isArray(data.groups)||!Array.isArray(data.teachers))throw new Error('Не удалось загрузить список.');
                catalog={groups:[...new Set(data.groups.filter(x=>typeof x==='string'&&x.trim()))],teachers:[...new Set(data.teachers.filter(x=>typeof x==='string'&&x.trim()))]};
                catalogFailed=false;return catalog;
            }).catch(e=>{catalogFailed=true;catalogPromise=null;throw e;});
        }
        return catalogPromise;
    }
    function resizeSheet() {
        if(!sheet?.open)return;
        const viewport=window.visualViewport;
        sheet.style.setProperty('--entity-viewport-height',(viewport?.height||innerHeight)+'px');
        sheet.style.setProperty('--entity-keyboard-bottom',Math.max(0,innerHeight-(viewport?.height||innerHeight)-(viewport?.offsetTop||0))+'px');
    }
    function openAddModal() {
        if(saving)return;closeDropdown();
        if(!sheet)createAddModal();
        $('entitySelectorStatus')?.remove();input.value='';error.textContent='';selected=null;entityType='group';catalogFailed=false;
        updateType();oldOverflow=document.body.style.overflow;document.body.style.overflow='hidden';
        if(!sheet.open)sheet.showModal();tg?.BackButton?.onClick?.(closeAddModal);tg?.BackButton?.show?.();resizeSheet();input.focus({preventScroll:true});onAddInputChange();
    }
    function closeAddModal() {if(sheet?.open)sheet.close();}
    document.addEventListener('visibilitychange',()=>{if(!document.hidden&&sheet?.open)tg?.BackButton?.show?.();});
    function setSaving() {
        if(!sheet)return;
        manage.disabled=saving;
        confirmButton.disabled=saving||!selected;confirmButton.textContent=saving?'Сохраняем…':'Сохранить';input.disabled=saving;
        sheet.querySelectorAll('[data-entity-type]').forEach(b=>{b.disabled=saving;});
        results.querySelectorAll('button').forEach(b=>{b.disabled=saving||b.getAttribute('aria-disabled')==='true';});
    }
    function updateType() {
        sheet.querySelectorAll('[data-entity-type]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.entityType===entityType)));
        const title=entityType==='group'?'Группа':'Преподаватель';fieldLabel.textContent=title;input.setAttribute('aria-label',title);
        input.placeholder=entityType==='group'?'Номер группы':'Фамилия преподавателя';
    }
    function createAddModal() {
        sheet=node('dialog','entity-sheet');sheet.id='entityAddDialog';sheet.setAttribute('aria-labelledby','entityAddTitle');
        const header=node('div','entity-sheet-header'),heading=node('h2','','Сохранить расписание');heading.id='entityAddTitle';
        const close=button('',closeAddModal,'entity-sheet-close');close.append(icon('close'));close.setAttribute('aria-label','Закрыть');header.append(heading,close);
        const body=node('div','entity-sheet-body'),types=node('div','entity-type-switch');types.setAttribute('role','group');types.setAttribute('aria-label','Что добавить');
        for(const [type,title] of [['group','Группа'],['teacher','Преподаватель']]){const b=button(title,()=>{if(entityType===type)return;entityType=type;input.value='';updateType();onAddInputChange();input.focus({preventScroll:true});},'');b.dataset.entityType=type;types.append(b);}
        const field=node('label','entity-search-field');fieldLabel=node('span','');input=node('input','');input.id='addInput';input.type='search';input.autocomplete='off';input.spellcheck=false;
        input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-controls','addAutocomplete');input.setAttribute('aria-expanded','false');field.append(fieldLabel,input);
        status=node('p','entity-search-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
        retry=button('Повторить',()=>{catalogFailed=false;onAddInputChange();},'entity-search-retry');retry.hidden=true;
        results=node('div','entity-search-results');results.id='addAutocomplete';results.setAttribute('role','listbox');results.setAttribute('aria-label','Подсказки');
        error=node('p','entity-selector-error');error.setAttribute('role','alert');body.append(types,field,status,retry,results,error);
        const footer=node('div','entity-sheet-footer');confirmButton=button('Сохранить',addFavorite,'entity-save-button');footer.append(confirmButton);sheet.append(header,body,footer);document.body.append(sheet);
        input.addEventListener('input',onAddInputChange);
        input.addEventListener('keydown',event=>{
            if(event.key==='Escape'){event.preventDefault();closeAddModal();return;}
            const options=[...results.querySelectorAll('button:not(:disabled)')];
            if(['ArrowDown','ArrowUp'].includes(event.key)&&options.length){event.preventDefault();activeOption=event.key==='ArrowDown'?(activeOption+1)%options.length:(activeOption<0?options.length-1:(activeOption-1+options.length)%options.length);options.forEach((b,i)=>b.classList.toggle('is-active',i===activeOption));input.setAttribute('aria-activedescendant',options[activeOption].id);options[activeOption].scrollIntoView({block:'nearest'});}
            else if(event.key==='Enter'){event.preventDefault();if(activeOption>=0)options[activeOption]?.click();else if(selected)addFavorite();}
        });
        sheet.addEventListener('click',event=>{if(event.target!==sheet)return;const rect=sheet.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeAddModal();});
        sheet.addEventListener('close',()=>{tg?.BackButton?.offClick?.(closeAddModal);tg?.BackButton?.hide?.();searchVersion++;document.body.style.overflow=oldOverflow;input.removeAttribute('aria-activedescendant');manage.focus({preventScroll:true});});
    }
    function normalizeSearch(value) {return value.normalize('NFKC').toLocaleLowerCase('ru').replace(/ё/g,'е').replace(/[\s.\-‐‑‒–—−]/g,'');}
    function onAddInputChange() {
        const version=++searchVersion;selected=null;activeOption=-1;confirmButton.disabled=true;error.textContent='';retry.hidden=true;input.removeAttribute('aria-activedescendant');
        const query=input.value.trim(),type=entityType;
        function render(){if(version===searchVersion&&sheet.open&&type===entityType)renderSearchResults(type,query);}
        if(catalog){render();return;}
        results.replaceChildren();input.setAttribute('aria-expanded','false');
        if(catalogFailed){status.textContent='Не удалось загрузить список.';retry.hidden=false;return;}
        status.textContent='Загружаем список…';
        loadScheduleCatalog().then(render).catch(()=>{if(version===searchVersion&&sheet.open){status.textContent='Не удалось загрузить список.';retry.hidden=false;}});
    }
    function renderSearchResults(type,query) {
        const needle=normalizeSearch(query),items=(type==='group'?catalog.groups:catalog.teachers).filter(name=>normalizeSearch(name).includes(needle));
        items.sort((a,b)=>Number(!normalizeSearch(a).startsWith(needle))-Number(!normalizeSearch(b).startsWith(needle))||a.localeCompare(b,'ru',{numeric:true}));
        results.replaceChildren();status.textContent=items.length?'':query?'Ничего не найдено.':type==='group'?'Пока нет групп в каталоге.':'Пока нет преподавателей в каталоге.';
        items.slice(0,20).forEach((name,index)=>{
            const entity={entity_type:type,entity_id:name,entity_name:name},existing=tracked().some(e=>same(e,entity));
            const option=button('',()=>{selected=entity;input.value=name;input.removeAttribute('aria-activedescendant');activeOption=-1;confirmButton.disabled=false;results.querySelectorAll('button').forEach(b=>{b.classList.remove('is-active');b.setAttribute('aria-selected',String(b===option));});},'entity-search-option');
            option.id='entityOption'+index;option.tabIndex=-1;option.setAttribute('role','option');option.setAttribute('aria-selected',String(same(entity,selected)));option.append(node('span','',name));
            if(existing){option.disabled=true;option.setAttribute('aria-disabled','true');option.append(node('small','',same(entity,userEntity)?'Моё расписание':'Уже сохранено'));}
            results.append(option);
        });input.setAttribute('aria-expanded',String(items.length>0));
    }
}());
