import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks';
import { ArrowLeft, ArrowUpRight, CalendarDays, Check, ChevronDown, Coffee, FileQuestion, Orbit, PartyPopper, Send, Plus, Sparkles, Star } from 'lucide-preact';
import { CatalogControls } from './catalog';
import { FavoriteRow, useFavoriteChange } from './favorites';
import {
    canWarmSchedules, haptic, holiday, loadPrediction, loadSchedule, peek, predictionKey, reactionsFor, scheduleKey,
    shift, studyDay, store, toggleReaction, useQuery, warmSchedule,
} from './data';
import { homeScheduleDay, parseLessons, progressDisplay, progressFormats, timeState } from './schedule';
import { DayPager } from './day-pager';
import { DateHeader, Empty, ForecastSchedule, LessonDetails, LessonList, PdfLink, PracticeForecastPause, ScheduleSkeleton, Sheet, openBot } from './ui';

const EMOJIS = ['👍','👎','❤️','🔥','😂','😮','😢','😡','🎉','👏','🙏','👀','🤔','🤮','💩','👻','👽','🤖','🤡','🍑'];
const DEFAULT_EMOJIS = ['👍','❤️','🔥'];
const CUSTOM_REACTIONS = new Map([
    ['AAA.webm', { file:'AAA.gif', label:'ААА', fallback:'✨' }],
    ['mirbi.gif', { file:'mirbi.gif', label:'Мирби', fallback:'🙂' }],
    ['smeshno.gif', { file:'smeshno.gif', label:'Смешно', fallback:'😂' }],
]);
const PICKER_REACTIONS = [...CUSTOM_REACTIONS.keys(), ...EMOJIS];
const timerSurprises = [
    { name:'aurora', icon:Sparkles },
    { name:'orbit', icon:Orbit },
    { name:'party', icon:PartyPopper },
];

function ReactionIcon({ reaction }) {
    const [failed, setFailed] = useState(false);
    const custom = CUSTOM_REACTIONS.get(reaction);
    if (custom && !failed && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        return <img className="reaction-glyph" src={'/static/emoji/' + custom.file} alt=""
            decoding="async" onError={() => setFailed(true)} />;
    }
    return <span aria-hidden="true">{custom?.fallback || reaction}</span>;
}

function Ad() {
    const host = useRef(null);
    useLayoutEffect(() => {
        const originals = document.getElementById('miniapp-campaign-source');
        if (originals && host.current) {
            for (const node of [...originals.children]) host.current.append(node);
        }
    }, []);
    return <div className="miniapp-ad" ref={host} />;
}

function Progress({ today, lessons, loading, visible }) {
    const [time, setTime] = useState(Date.now);
    const [format, setFormat] = useState(0);
    const [surprise, setSurprise] = useState(null);
    const taps = useRef({ count:0, at:0 });
    const nextSurprise = useRef(0);
    const flown = useRef('');
    useEffect(() => {
        if (surprise?.name !== 'plane') return;
        const timer = setTimeout(() => setSurprise(null), 1300);
        return () => clearTimeout(timer);
    }, [surprise]);
    useEffect(() => {
        if (!visible) return;
        function tick() { if (!document.hidden) setTime(Date.now()); }
        tick();
        const timer = setInterval(tick, 1000);
        document.addEventListener('visibilitychange', tick);
        return () => {
            clearInterval(timer);
            document.removeEventListener('visibilitychange', tick);
        };
    }, [visible]);
    const state = timeState(today, lessons, time);
    const rest = state?.kind === 'done' || holiday(today);
    const progress = rest ? 1 : state?.progress || 0;
    const mode = progressFormats[format];
    const { info, label } = progressDisplay(mode, state, time, rest, loading);
    const Surprise = surprise?.icon;
    return <section className="progress-card" data-surprise={surprise?.name} aria-label="Время до пары">
        <div className="progress-remaining" role="progressbar" aria-label="Прогресс" aria-valuenow={Math.round(progress * 100)}
            style={{ transform: 'scaleX(' + (1 - progress) + ')' }} />
        <button className="progress-copy" id="progress_bar" type="button" title="Сменить формат времени" data-time-format={mode} onClick={() => {
            setFormat(index => (index + 1) % progressFormats.length);
            const at = Date.now();
            setTime(at);
            const count = at - taps.current.at < 650 ? taps.current.count + 1 : 1;
            taps.current = { count:count % 5, at };
            haptic();
            if (state?.kind === 'done' && flown.current !== today) {
                let lastFlight;
                try { lastFlight = localStorage.getItem('kkepik:last-flight'); } catch {}
                flown.current = today;
                if (lastFlight !== today) {
                    try { localStorage.setItem('kkepik:last-flight', today); } catch {}
                    setSurprise({ name: 'plane', icon: Send });
                    return;
                }
            }
            if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
                if (count === 5) setSurprise(timerSurprises[nextSurprise.current++ % timerSurprises.length]);
                if (count === 1 || count === 5) window.launchSideConfetti?.({ forceForInteraction: true });
            }
        }}>
            <strong>{info}</strong>
            {label && <span>{label}</span>}
        </button>
        {Surprise && <div className="timer-surprise" key={surprise.name} aria-hidden="true"
            onAnimationEnd={() => setSurprise(null)}><Surprise size={32} /></div>}
    </section>;
}

function ScheduleActions({ iso, type, entity, visible }) {
    const key = 'reaction:' + iso;
    const [data,, retry] = useQuery(visible ? key : null, () => reactionsFor(iso), { ttl: 45000 });
    const [busy, setBusy] = useState(false);
    const [picker, setPicker] = useState(false);
    const [error, setError] = useState('');
    const ongoing = useRef(false);
    const reactionRow = useRef(null);
    useEffect(() => { setPicker(false); setError(''); }, [iso]);
    const counts = data?.reactions || [];
    const own = data?.user_reactions?.[0];
    const popular = counts.filter(item => item.count > 0).sort((a, b) => b.count - a.count);
    const active = [...new Set([...popular.map(item => item.reaction), ...DEFAULT_EMOJIS])].slice(0, 3);
    if (own && !active.includes(own)) active[active.length - 1] = own;
    async function react(emoji) {
        if (ongoing.current) return;
        ongoing.current = true;
        setBusy(true); setError(''); haptic('light');
        const previous = peek(key);
        const before = previous?.user_reactions?.[0];
        const after = before === emoji ? '' : emoji;
        const next = new Map((previous?.reactions || []).map(({ reaction, count }) => [reaction, count]));
        if (before) next.set(before, Math.max(0, (next.get(before) || 0) - 1));
        if (after) next.set(after, (next.get(after) || 0) + 1);
        store(key, { reactions: [...next].map(([reaction, count]) => ({ reaction, count })), user_reactions: after ? [after] : [] });
        try {
            const result = await toggleReaction(iso, emoji);
            store(key, result);
            if (result.user_reactions?.includes(emoji)) requestAnimationFrame(() => {
                const target = Array.from(reactionRow.current?.children || []).find(button =>
                    button.dataset.reaction === emoji && button.dataset.date === iso);
                if (target) window.launchReactionBurst?.(target, CUSTOM_REACTIONS.get(emoji)?.fallback || emoji);
            });
        } catch {
            if (previous) store(key, previous); else retry();
            haptic('error');
            setError('Не удалось сохранить реакцию.');
        } finally {
            ongoing.current = false;
            setBusy(false);
        }
    }
    return <div className="schedule-actions">
        <div className="schedule-action-row">
            <PdfLink iso={iso} type={type} entity={entity} />
            <div className="reactions" ref={reactionRow} role="group" aria-label="Реакции на расписание">
                {active.map(emoji => {
                    const count = counts.find(item => item.reaction === emoji)?.count || 0;
                    const label = CUSTOM_REACTIONS.get(emoji)?.label || emoji;
                    return <button key={emoji} type="button" className="reaction" data-reaction={emoji} data-date={iso}
                        aria-pressed={own === emoji} aria-label={label + (count ? ' · ' + count : '')} disabled={busy} onClick={() => react(emoji)}>
                        <ReactionIcon reaction={emoji} />{count > 0 && <small>{count}</small>}
                    </button>;
                })}
                <button type="button" className="reaction-more" aria-label="Ещё реакции" disabled={busy} onClick={() => { haptic('soft'); setPicker(true); }}><Plus size={18} aria-hidden="true" /></button>
            </div>
        </div>
        {error && <p className="form-error" role="status">{error}</p>}
        {picker && <Sheet title="Выбрать реакцию" onClose={() => setPicker(false)}>{dismiss =>
            <div className="reaction-picker">{PICKER_REACTIONS.map(emoji => <button key={emoji} type="button"
                aria-pressed={own === emoji} aria-label={CUSTOM_REACTIONS.get(emoji)?.label || emoji} onClick={() => dismiss(() => react(emoji))}>
                <ReactionIcon reaction={emoji} />
            </button>)}</div>
        }</Sheet>}
    </div>;
}

function FavoriteChooser({ primary, primaryType, current, favorites, onChoose, onClose }) {
    const [adding, setAdding] = useState(false);
    const [type, setType] = useState('group');
    const [text, setText] = useState('');
    const { change, busy, error, clearError } = useFavoriteChange();
    async function add(name, dismiss) {
        if (busy) return;
        if (primaryType === type && primary === name) { dismiss(() => onChoose(null)); return; }
        const entry = { entity_type:type, entity_id:name, entity_name:name };
        const existing = favorites.find(item => item.entity_type === type && item.entity_id === name);
        if (existing) { dismiss(() => onChoose(existing)); return; }
        if (await change('add', entry)) dismiss(() => onChoose(entry, false));
    }

    return <Sheet title={adding ? 'Добавить расписание' : 'Расписания'} onClose={onClose}>{dismiss =>
        <div className="favorite-sheet" key={adding ? 'add' : 'list'}>
            {adding ? <>
                <button type="button" className="text-button" disabled={busy} onClick={() => { haptic('soft'); setAdding(false); clearError(); }}>
                    <ArrowLeft size={17} aria-hidden="true" /> К избранному
                </button>
                <CatalogControls type={type} text={text} visible favoritesOnly busy={busy}
                    onType={value => { if (value === type) return; haptic(); setType(value); setText(''); clearError(); }}
                    onEdit={value => { setText(value); clearError(); }} onPick={name => add(name, dismiss)} />
            </> : <>
                <div className="sheet-list">
                    <button type="button" className="choice-row" aria-pressed={!current} onClick={() => dismiss(() => onChoose(null))}>
                        <span className="catalog-icon"><CalendarDays size={17} aria-hidden="true" /></span><span>{primary}</span>
                        {!current && <Check size={17} aria-hidden="true" />}
                    </button>
                    {favorites.map(item => {
                        const selected = current?.entity_type === item.entity_type && current?.entity_id === item.entity_id;
                        return <FavoriteRow key={item.entity_type + ':' + item.entity_id} item={item} selected={selected} busy={busy}
                            onChoose={() => dismiss(() => onChoose(item))} onRemove={() => change('remove', item)} />;
                    })}
                </div>
                <button type="button" className="choice-row add-favorite" disabled={busy}
                    onClick={() => { haptic('soft'); setAdding(true); setText(''); clearError(); }}>
                    <span className="catalog-icon"><Plus size={17} aria-hidden="true" /></span><span>Добавить в избранное</span>
                </button>
            </>}
            {busy && <p className="hint" role="status">Сохраняем…</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
        </div>
    }</Sheet>;
}

export function Home({ now, today, boot, error, retry, favorites, visible }) {
    const [chosenDate, setChosenDate] = useState(null);
    const [entity, setEntity] = useState(null);
    const [chooser, setChooser] = useState(false);
    const [openedLesson, setOpenedLesson] = useState(null);
    const type = entity?.entity_type || 'me';
    const entityId = entity?.entity_id || '';
    const name = entity?.entity_name || boot?.user?.name_or_group;
    const allowSchedule = !!boot?.user || Boolean(entity);
    const todayKey = scheduleKey(today, type, entityId);
    const [current, currentError, retryCurrent] = useQuery(allowSchedule && visible ? todayKey : null,
        () => loadSchedule(today, type, entityId), { ttl: 45000 });
    const currentSchedule = current || peek(todayKey);
    const roleType = type !== 'me' ? type : boot?.user?.role === 'teacher' || currentSchedule?.teacher ? 'teacher' : 'group';
    const currentLessons = useMemo(() => parseLessons(currentSchedule?.schedule, roleType), [currentSchedule, roleType]);
    const automaticDate = homeScheduleDay(today, studyDay(today, 1), currentLessons,
        Array.isArray(currentSchedule?.schedule), holiday(today), now);
    const date = chosenDate ?? automaticDate;
    const dayHeading = date === today ? 'Сегодня' : date === shift(today, 1) ? 'Завтра' : undefined;
    const selectedKey = date !== today && allowSchedule && visible ? scheduleKey(date, type, entityId) : null;
    const [schedule, selectedError, retrySelected] = useQuery(selectedKey,
        () => loadSchedule(date, type, entityId), { ttl: 45000 });
    const data = date === today ? currentSchedule : schedule || (!boot && !error && peek(scheduleKey(date, type, entityId)));
    const scheduleError = date === today ? currentError : selectedError;
    const retrySchedule = date === today ? retryCurrent : retrySelected;
    const lessons = useMemo(() => parseLessons(data?.schedule, roleType), [data, roleType]);
    const homeSchedule = peek(scheduleKey(today));
    const timerLessons = useMemo(() => parseLessons(homeSchedule?.schedule, boot?.user?.role === 'teacher' ? 'teacher' : 'group'), [homeSchedule, boot?.user?.role]);
    const upcoming = date >= today && date <= shift(today, 7) && !holiday(date);
    const forecastKey = visible && upcoming && data?.unavailable && roleType === 'group' && name ? predictionKey(date, name) : null;
    const [forecast, forecastError] = useQuery(forecastKey, () => loadPrediction(date, name), { ttl: 120000 });
    const preliminary = forecast?.status === 'preliminary' && Boolean(data?.unavailable);
    const loading = !data && !error && !scheduleError && (!boot || !!boot.user);
    useEffect(() => {
        if (!visible || !data || !canWarmSchedules()) return;
        const timer = setTimeout(() => {
            for (const delta of [-1, 1]) warmSchedule(studyDay(date, delta), type, entityId).catch(() => {});
        }, 170);
        return () => clearTimeout(timer);
    }, [visible, date, type, entityId, !!data]);
    useEffect(() => {
        if (!visible || !upcoming || !data?.unavailable) return;
        const timer = setInterval(() => {
            if (!document.hidden && navigator.onLine) retrySchedule();
        }, 75000);
        return () => clearInterval(timer);
    }, [visible, upcoming, date, type, entityId, !!data?.unavailable]);
    useEffect(() => {
        if (visible && data && !data.unavailable && !data.holiday) {
            window.kkepikActivity?.shown(data, date, lessons.length ? 'view' : 'empty');
        }
    }, [visible, data, date, lessons.length]);
    useEffect(() => {
        if (entity && !favorites.some(f => f.entity_id === entity.entity_id && f.entity_type === entity.entity_type)) {
            setEntity(null);
            setOpenedLesson(null);
        }
    }, [favorites]);
    useEffect(() => { setOpenedLesson(null); }, [date]);
    function jump(next) {
        if (next === date) return;
        haptic(); setChosenDate(next === automaticDate ? null : next);
    }
    function selectDay(delta) { jump(studyDay(date, delta)); }
    function select(item, feedback = true) {
        if (feedback) haptic();
        setEntity(item); setOpenedLesson(null);
        if (item) window.kkepikActivity?.selected(item, true);
    }
    return <>
        <h1 className="screen-reader-only">Расписание</h1>
        <Ad />
        <div className="home-controls">
            {(name || (!boot && !error)) && <div className="schedule-owner">
                {name ? <button className="entity-button" type="button" aria-haspopup="dialog" aria-expanded={chooser && visible} onClick={() => { haptic('soft'); setChooser(true); }}>
                    <span>{name}</span><ChevronDown size={16} aria-hidden="true" />
                </button> : <span className="owner-skeleton" aria-hidden="true" />}
            </div>}
            <Progress today={today} lessons={timerLessons} loading={!boot && !error} visible={visible} />
        </div>
        <section className="schedule-section">
            <DateHeader iso={date} label={dayHeading}
                onPrev={() => selectDay(-1)} onNext={() => selectDay(1)} onDate={jump}
                onToday={date === today ? null : () => jump(today)} />
            <DayPager iso={date} type={type} entity={entityId} name={name} role={roleType} onDay={selectDay} enabled={visible && (!!data || !!chosenDate)}>
            {loading && <ScheduleSkeleton />}
            {!data && !!(scheduleError || error) && <Empty icon={FileQuestion} title="Расписание не загрузилось" retry={scheduleError ? retrySchedule : retry}>
                Проверь подключение и попробуй ещё раз.
            </Empty>}
            {!data && boot && !boot.user && !entity && <Empty title={boot.user_status === 404 ? 'Выбери группу в боте' : 'Профиль не загрузился'}
                retry={boot.user_status === 404 ? null : retry}>
                {boot.user_status === 404 && <button className="text-button" onClick={openBot}>Открыть бота <ArrowUpRight size={15} /></button>}
            </Empty>}
            {data && lessons.length > 0 && <>
                <LessonList iso={date} lessons={lessons} type={roleType} entity={name || data?.[roleType]}
                    now={date === today ? now : undefined}
                    onLesson={(n, rows) => { haptic(n === 5 ? 'medium' : 'soft'); setOpenedLesson({ date, n, rows }); }} />
                {scheduleError && <p className="stale-note">Показана сохранённая копия.</p>}
                <ScheduleActions iso={date} type={roleType} entity={name || data?.[roleType]} visible={visible} />
            </>}
            {preliminary && <ForecastSchedule iso={date} group={name} forecast={forecast}
                onLesson={(n, rows) => { haptic('soft'); setOpenedLesson({ date, n, rows, preliminary: true }); }} />}
            {forecastKey && !forecast && !forecastError && navigator.onLine && <ScheduleSkeleton />}
            {data && !lessons.length && !preliminary && (!forecastKey || forecast || forecastError || !navigator.onLine) && <div className="swipe-empty">
                {forecastKey && forecast?.reason === 'practice' ? <PracticeForecastPause /> : <Empty icon={data.unavailable ? FileQuestion : Coffee} title={data.unavailable ? 'Расписание пока недоступно' : 'Пар нет'}>
                    {data.unavailable && 'Когда опубликуют расписание, оно появится здесь.'}
                </Empty>}
            </div>}
            </DayPager>
        </section>
        {openedLesson?.date === date && visible && (!openedLesson.preliminary || preliminary)
            && <LessonDetails lesson={openedLesson} onClose={() => setOpenedLesson(null)} />}
        {chooser && visible && <FavoriteChooser primary={boot?.user?.name_or_group || 'Моё расписание'}
            primaryType={boot?.user?.role === 'teacher' ? 'teacher' : 'group'}
            current={entity} favorites={favorites} onChoose={select} onClose={() => setChooser(false)} />}
    </>;
}
