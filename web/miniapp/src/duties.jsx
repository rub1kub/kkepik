import { useState, useRef } from 'preact/hooks';
import { Bell, Calendar, Check, ClipboardList, Coffee, MapPin, Settings2, Users } from 'lucide-preact';
import { dateAtNoon, dateLabel, dutyAction, dutyDetail, dutyList, haptic, holiday, store, useQuery } from './data';
import { Empty, RouteLink, Sheet, Skeleton } from './ui';

const activeStates = new Set(['assigned','reported','done']);
const participants = day => (day?.people || []).filter(person => !person.state || activeStates.has(person.state));

function Names({ people }) {
    return <div className="duty-names">{people.map((person, index) => <div className="duty-person" key={person.member || person.id || index}>
        <span className="name-avatar" aria-hidden="true">{Array.from(person.name)[0]?.toLocaleUpperCase('ru')}</span>
        <strong>{person.name}</strong>{person.mine && <span className="small-chip">Ты</span>}
    </div>)}</div>;
}

export function Duties({ today, now, visible }) {
    const params = new URLSearchParams(location.search);
    const [sidOverride, setSid] = useState(() => Number(params.get('space')) || null);
    const [view, setView] = useState(() => params.get('tab') === 'duty' && params.get('view') === 'calendar' ? 'calendar' : 'today');
    const [error, setError] = useState('');
    const [working, setWorking] = useState(false);
    const [confirm, setConfirm] = useState(null);
    const [reason, setReason] = useState('');
    const inFlight = useRef(false);
    const [list, listError, retry] = useQuery(visible ? 'duty-list' : null, dutyList, { ttl: 45000 });
    const sid = (list?.spaces || []).some(space => space.id === sidOverride) ? sidOverride : list?.spaces[0]?.id;
    const key = 'duty:' + sid;
    const [state, stateError, retryState] = useQuery(visible && sid ? key : null, () => dutyDetail(sid), { ttl: 45000 });
    const day = state?.days?.find(item => item.date === today);
    const planned = !day && state?.forecast?.find(item => item.date === today);
    const todayPeople = participants(day || planned);
    const mine = day?.people?.find(item => item.mine);
    const beforeCutoff = day && now / 1000 < day.cutoff && state.status === 'active';
    const waitingDecision = beforeCutoff && day.my_request === 'pending';
    const openRequests = beforeCutoff ? day.requests?.filter(r => !r.mine).length || 0 : 0;
    const weekday = (dateAtNoon(today).getUTCDay() + 6) % 7;
    const off = day?.status === 'no_duty' || planned?.status === 'no_duty'
        || (!day && (holiday(today) || !state?.options?.days?.includes(weekday)));
    let caption = 'Сегодня нужна замена';
    if (state?.status === 'paused') caption = 'Дежурства на паузе';
    else if (state?.first_date > today) caption = 'Дежурства начнутся ' + dateLabel(state.first_date);
    else if (off) caption = 'Сегодня без дежурства';
    else if (day?.status === 'cancelled') caption = 'Дежурство отменено';
    else if (day?.status === 'closed') caption = 'Сегодня отдежурили';
    else if (mine?.state === 'assigned') caption = 'Ты сегодня дежуришь';
    else if (mine?.state === 'reported') caption = 'Ждём подтверждения старосты';
    else if (['declined', 'released'].includes(mine?.state)) caption = 'Твоё дежурство перенесено';
    else if (day?.status === 'searching') caption = 'Подбираем замену';
    else if (todayPeople.length) caption = day ? 'Сегодня дежурят' : 'По графику сегодня дежурят';
    const future = state?.forecast?.filter(item => item.date > today && participants(item).length) || [];
    const personal = future.find(item => participants(item).some(person => person.mine));
    const past = state?.days?.filter(item => item.date < today && item.people.some(person => person.state === 'done')) || [];
    function startRefusal(body) {
        haptic('soft');
        setError('');
        setReason('');
        setConfirm(body);
    }
    async function mutate(body) {
        if (inFlight.current) return false;
        inFlight.current = true;
        let applied = false;
        setWorking(true); setError('');
        try {
            await dutyAction(sid, body);
            applied = true;
            haptic('success');
            if (body.action === 'daily_notifications') store(key, { ...state, daily_notifications: body.value });
            store(key, await dutyDetail(sid));
        } catch (problem) {
            if (!applied) haptic('error');
            setError(problem.message);
        } finally { inFlight.current = false; setWorking(false); }
        return applied;
    }
    return <div className="duty-pane">
        <header className="section-heading duty-heading">
            <span className="section-mark"><Users size={21} aria-hidden="true" /></span>
            <div><h1>Дежурство</h1>{list?.spaces?.length > 1 ?
            <select className="space-select" aria-label="Группа дежурства" value={sid} onChange={e => setSid(Number(e.currentTarget.value))}>
                {list.spaces.map(space => <option key={space.id} value={space.id}>{space.group}</option>)}
            </select> : list?.group && <span className="duty-group">{list.group}</span>}</div>
        </header>
        {!list && !listError && <Skeleton count={2} />}
        {listError && !list && <Empty icon={ClipboardList} title="Дежурства не загрузились" retry={retry}>{listError.message}</Empty>}
        {list && !sid && <>
            {list.can_create === false
                ? <Empty icon={ClipboardList} title="Дежурства уже настроены">Попроси старосту добавить тебя в список.</Empty>
                : <><Empty icon={ClipboardList} title="Списка дежурств пока нет">Староста может добавить пары и настроить очередь для группы.</Empty>
                    <RouteLink icon={Settings2} href="/duties">Настроить список</RouteLink></>}
        </>}
        {sid && !state && !stateError && <Skeleton count={2} />}
        {sid && stateError && !state && <Empty title="Не удалось открыть список" retry={retryState}>{stateError.message}</Empty>}
        {state && <>
            <div className="segments" aria-label="Вид дежурств">
                {[['today', 'Сегодня'], ['calendar', 'График']].map(([id,label]) => <button key={id} aria-pressed={view === id} onClick={() => { if (id !== view) { haptic(); setView(id); } }}>{label}</button>)}
            </div>
            {view === 'today' ? <div className="duty-stack" key="today">
                <section className="duty-card duty-today">
                    <div className="duty-stamp"><span className="card-icon">{off ? <Coffee size={23} /> : <ClipboardList size={23} />}</span><span>{dateLabel(today)}</span></div>
                    <h2>{caption}</h2>
                    <Names people={state.status === 'paused' ? [] : todayPeople} />
                    {mine?.state === 'assigned' && <>
                        {state.options?.place && <p className="duty-place"><MapPin size={15} /> {state.options.place}</p>}
                        {state.options?.tasks && <p>{state.options.tasks}</p>}
                        <div className="duty-actions">
                            {now / 1000 >= day.report_at && state.status === 'active' && <button className="primary-button" disabled={working} onClick={() => mutate({action:'done',day:day.id})}><Check size={17}/> Отдежурил(а)</button>}
                            {beforeCutoff && day.can_request && <button className="soft-button" disabled={working} onClick={() => startRefusal({action:'decline',day:day.id})}>Сегодня не могу</button>}
                        </div>
                    </>}
                    {waitingDecision && <><p>Запрос у старосты. Пока ты дежуришь.</p>
                        <button className="text-button" disabled={working} onClick={() => mutate({action:'reconsider',day:day.id})}>Отозвать запрос</button></>}
                    {mine?.state === 'assigned' && day.my_request === 'rejected' && <p>Староста отклонил запрос. Дежурство остаётся за тобой.</p>}
                    {personal && state.status !== 'paused' && <div className="duty-next">
                        <span>Твоя следующая очередь</span><strong>{dateLabel(personal.date)}</strong>
                        {personal.tentative && <small>Предварительно</small>}
                        <Names people={participants(personal).filter(person => !person.mine)} />
                    </div>}
                </section>
                {(day?.offers || []).filter(offer => now / 1000 < offer.expires).map(offer => <section className="duty-card" key={offer.id}>
                    <h3>Нужна подмена</h3><p>{offer.host} и {offer.guest}</p>
                    {offer.answered ? <p className="hint">Ждём ответа напарника.</p> : <div className="duty-actions">
                        <button className="primary-button" disabled={working} onClick={() => mutate({action:'offer_yes',offer:offer.id})}>Смогу</button>
                        <button className="soft-button" disabled={working} onClick={() => startRefusal({action:'offer_no',offer:offer.id})}>Не могу</button>
                    </div>}
                </section>)}

            </div> : <div className="duty-stack" key="calendar">
                {future.length ? future.map(item => <section key={item.date} className="duty-card">
                    <div className="duty-date"><Calendar size={17}/><h3>{dateLabel(item.date)}</h3>{item.tentative && <span className="small-chip">Предварительно</span>}</div>
                    <Names people={participants(item)} />
                </section>) : <Empty icon={Calendar} title="Ближайшая очередь не назначена" />}
                {!!past.length && <details className="duty-card duty-history">
                    <summary>Прошлые дежурства</summary>
                    {past.map(item => <div key={item.id}><p>{dateLabel(item.date)}</p>
                        <Names people={item.people.filter(person => person.state === 'done')} /></div>)}
                </details>}
            </div>}
            <div className="duty-options">
                <label className="setting-row"><Bell size={20} /><span>Кто сегодня дежурит<br /><small>Уведомления в Telegram</small></span>
                    <input type="checkbox" role="switch" aria-label="Уведомления о дежурных" checked={!state.muted && state.daily_notifications} disabled={working || state.muted}
                        onChange={event => mutate({action:'daily_notifications',value:event.currentTarget.checked})} />
                </label>
                <RouteLink href={'/duties?view=' + (state.role === 'member' ? 'members' : 'assignments') + '&space=' + sid}
                    icon={state.role === 'member' ? Users : Settings2}>
                    {state.role === 'member' ? 'Пары и запасные' : openRequests ? 'Управление · есть запросы' : 'Управление списком'}
                </RouteLink>
            </div>
        </>}
        {error && !confirm && <p className="form-error" role="alert">{error}</p>}
        {confirm && visible && <Sheet title={confirm.action === 'decline' ? 'Попросить перенос' : 'Отказаться от подмены'}
            onClose={() => setConfirm(null)}>{dismiss => <form className="duty-reason-form" onSubmit={async event => {
                event.preventDefault();
                if (reason.trim() && await mutate({ ...confirm, reason: reason.trim() })) dismiss();
            }}>
                <label>Причина<textarea rows={3} maxLength={500} required value={reason} disabled={working}
                    onInput={event => setReason(event.currentTarget.value)} /></label>
                <p className="hint">{confirm.action === 'decline'
                    ? 'Староста и заместители увидят причину. Пока запрос рассматривают, ты остаёшься дежурным.'
                    : 'Причину увидят староста и заместители.'}</p>
                {error && <p className="form-error" role="alert">{error}</p>}
                <button className="primary-button" type="submit" disabled={working || !reason.trim()}>
                    {confirm.action === 'decline' ? 'Отправить запрос' : 'Отправить отказ'}
                </button>
            </form>}</Sheet>}
    </div>;
}
