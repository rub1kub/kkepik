import { useEffect, useId, useMemo, useRef, useState } from 'preact/hooks';
import { ArrowUpRight, CalendarDays, ChevronDown, Clock3, ChevronLeft, ChevronRight, Copy, Download, RefreshCw } from 'lucide-preact';
import { apiDate, dateAtNoon, dateLabel, haptic, tg, today } from './data';
import { pairTimes, shortSubjectName, timeState } from './schedule';

export function IconButton({ icon: Icon, label, className = '', ...props }) {
    return <button type="button" aria-label={label} title={label} className={'icon-button ' + className} {...props}><Icon size={19} aria-hidden="true" /></button>;
}

export function Skeleton({ count = 3 }) {
    return <div className="skeleton-list" aria-label="Загрузка" role="status">
        {Array.from({ length: count }, (_, index) => <div className="skeleton-row" key={index}><span /><div><i /><i /></div></div>)}
    </div>;
}

export function ScheduleSkeleton({ count = 3 }) {
    return <div className="lesson-list schedule-skeleton" role="status" aria-label="Загрузка расписания">
        {Array.from({ length: count }, (_, index) => <div className="lesson" key={index} aria-hidden="true">
            <i className="schedule-skeleton-time" />
            <div className="lesson-card">
                <i className="schedule-skeleton-title" />
                <i className="schedule-skeleton-note" />
                <i className="schedule-skeleton-room" />
            </div>
            <i className="schedule-skeleton-time" />
        </div>)}
    </div>;
}

export function Empty({ icon: Icon = CalendarDays, title, children, retry }) {
    return <section className="empty-card">
        <span className="card-icon"><Icon size={22} aria-hidden="true" /></span>
        <h3>{title}</h3>
        {children && <p>{children}</p>}
        {retry && <button className="soft-button" onClick={retry}><RefreshCw size={16} /> Повторить</button>}
    </section>;
}

const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const monthFormat = new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const weekdayFormat = new Intl.DateTimeFormat('ru-RU', { weekday: 'long', timeZone: 'UTC' });
const calendarDateFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const shortWeekDateFormat = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'long', timeZone: 'UTC' });

function DatePicker({ iso, onSelect, onClose }) {
    const [month, setMonth] = useState(() => iso.slice(0, 7) + '-01');
    const first = dateAtNoon(month);
    const year = first.getUTCFullYear();
    const index = first.getUTCMonth();
    const count = new Date(Date.UTC(year, index + 1, 0)).getUTCDate();
    const offset = (first.getUTCDay() + 6) % 7;
    const current = today();

    function moveMonth(delta) {
        haptic();
        setMonth(new Date(Date.UTC(year, index + delta, 1)).toISOString().slice(0, 10));
    }
    return <Sheet title="Выбрать дату" onClose={onClose}>{dismiss => <>
        <div className="calendar-header">
            <IconButton icon={ChevronLeft} label="Предыдущий месяц" onClick={() => moveMonth(-1)} />
            <div className="calendar-month" aria-live="polite">{monthFormat.format(first)}</div>
            <IconButton icon={ChevronRight} label="Следующий месяц" onClick={() => moveMonth(1)} />
        </div>
        <div className="calendar-grid">
            {weekdays.map(day => <span key={day} className="calendar-weekday">{day}</span>)}
            {Array.from({ length: count }, (_, i) => {
                const day = month.slice(0, 7) + '-' + String(i + 1).padStart(2, '0');
                return <button key={day} type="button" className="calendar-day" aria-label={dateLabel(day)}
                    aria-pressed={day === iso} aria-current={day === current ? 'date' : undefined}
                    style={i === 0 ? { gridColumnStart: offset + 1 } : undefined}
                    onClick={() => dismiss(() => onSelect(day))}>{i + 1}</button>;
            })}
        </div>
    </>}</Sheet>;
}

export function DateHeader({ iso, label, onPrev, onNext, onToday, onDate }) {
    const [picker, setPicker] = useState(false);
    const captionId = useId();
    const date = dateAtNoon(iso);
    const fullDate = dateLabel(iso);
    return <div className="date-box">
        <div className="date-row">
            <IconButton icon={ChevronLeft} label="Предыдущий день" onClick={onPrev} />
            <button type="button" className="date-button" aria-label={label || fullDate} aria-describedby={captionId}
                aria-haspopup="dialog" aria-expanded={picker}
                onClick={() => { haptic('soft'); setPicker(true); }}>
                <span className="date-title"><span>{label || weekdayFormat.format(date)}</span><CalendarDays size={16} aria-hidden="true" /></span>
                <span className="date-caption" id={captionId}><time dateTime={iso}>{label ? <>
                    <span className="date-wide">{fullDate}</span><span className="date-compact">{shortWeekDateFormat.format(date)}</span>
                </> : calendarDateFormat.format(date)}</time></span>
            </button>
            <IconButton icon={ChevronRight} label="Следующий день" onClick={onNext} />
        </div>
        <div className="date-return"><button type="button" className="text-button" disabled={!onToday} onClick={onToday}>К сегодня</button></div>
        {picker && <DatePicker iso={iso} onSelect={onDate} onClose={() => setPicker(false)} />}
    </div>;
}

export function Sheet({ title, badge, children, onClose }) {
    const dialog = useRef(null);
    const heading = useRef(null);
    const drag = useRef(null);
    const headingId = useId();
    useEffect(() => {
        const element = dialog.current;
        element.showModal();
        window.dispatchEvent(new Event('kkepik:sheet'));
        return () => {
            if (element.open) element.close();
            window.dispatchEvent(new Event('kkepik:sheet'));
        };
    }, []);
    useEffect(() => {
        if (dialog.current?.open && document.activeElement !== heading.current) heading.current.focus({ preventScroll:true });
    }, [title]);

    function dismiss(afterClose) {
        const element = dialog.current;
        if (!element?.open || element.hasAttribute('data-closing')) return;
        if (typeof afterClose !== 'function') haptic('soft');
        const done = () => {
            onClose();
            if (typeof afterClose === 'function') afterClose();
        };
        if (window.kkepikSlideClose) window.kkepikSlideClose(element, done);
        else { element.close(); done(); }
    }

    function startDrag(event) {
        if (!event.isPrimary || event.button !== 0 || event.target.closest('button') || dialog.current.hasAttribute('data-closing')) return;
        const element = dialog.current;
        drag.current = { id:event.pointerId, x:event.clientX, y:event.clientY, at:event.timeStamp };
        event.currentTarget.setPointerCapture?.(event.pointerId);
        element.dataset.dragging = '';
    }

    function moveDrag(event) {
        const start = drag.current;
        if (!start || start.id !== event.pointerId) return;
        const distance = event.clientY - start.y;
        dialog.current.style.setProperty('--sheet-offset', Math.max(0, distance) + 'px');
    }

    function endDrag(event, cancelled = false) {
        const start = drag.current;
        if (!start || start.id !== event.pointerId) return;
        const element = dialog.current;
        drag.current = null;
        delete element.dataset.dragging;
        const distance = event.clientY - start.y;
        const sideways = Math.abs(event.clientX - start.x);
        const quick = distance > 36 && distance / Math.max(1, event.timeStamp - start.at) > .55;
        if (cancelled || distance <= sideways || (distance < 80 && !quick)) {
            element.style.removeProperty('--sheet-offset');
            return;
        }
        dismiss();
    }

    return <dialog className="sheet" ref={dialog} aria-labelledby={headingId} onCancel={event => { event.preventDefault(); dismiss(); }}
        onClick={event => { if (event.target === event.currentTarget) dismiss(); }}>
        <div className="sheet-inner">
            <header className="sheet-header" onPointerDown={startDrag} onPointerMove={moveDrag}
                onPointerUp={endDrag} onPointerCancel={event => endDrag(event, true)}
                onLostPointerCapture={event => endDrag(event, true)}>
                <div className="sheet-handle" aria-hidden="true" />
                <div className="sheet-top">
                    <h2 className="sheet-title" id={headingId} ref={heading} tabIndex={-1} autofocus>{title}</h2>
                    {badge}
                    <button type="button" className="text-button" onClick={dismiss}>Готово</button>
                </div>
            </header>
            <div className="sheet-content">{typeof children === 'function' ? children(dismiss) : children}</div>
        </div>
    </dialog>;
}

export function ForecastSchedule({ iso, group, forecast, onLesson }) {
    const predictedLessons = useMemo(() => forecast?.lessons?.map(({ n, subject, probability, slot_probability, days_present, subject_options, possible_transfer }) => ({
        n, subject, probability, slotProbability: slot_probability, daysPresent: days_present, options: subject_options, possibleTransfer: possible_transfer,
        sampleDays: forecast.weekday_samples, teacher: [], rooms: [], groups: [],
    })) || [], [forecast]);
    return <div className="schedule-forecast">
        <details className="forecast-note">
            <summary tabIndex={onLesson ? undefined : -1}><Clock3 size={17} aria-hidden="true" />Предварительно<ChevronDown size={15} aria-hidden="true" /></summary>
            <p>Сверху — шанс самой пары. Листай предметы внутри карточки, чтобы увидеть варианты, если пара состоится. «Другой предмет» учитывает возможную замену.</p>
        </details>
        <LessonList iso={iso} lessons={predictedLessons} type="group" entity={group} onLesson={onLesson} />
    </div>;
}

export function PracticeForecastPause() {
    return <Empty title="Идёт практика">Прогноз на время практики скрыт. Здесь появится расписание, когда его опубликуют.</Empty>;
}

function ForecastCard({ iso, row, onLesson }) {
    const track = useRef(null);
    const settled = useRef(0);
    const settleTimer = useRef();
    useEffect(() => () => clearTimeout(settleTimer.current), []);
    const trackId = useId();
    const [position, setPosition] = useState(0);
    const options = row.options;
    const current = Math.min(position, options.length - 1);
    const multiple = options.length > 1;
    const when = pairTimes(iso, row.n).join('–');
    useEffect(() => {
        const element = track.current;
        if (element?.contains(document.activeElement)) element.children[current]?.focus({ preventScroll: true });
    }, [current]);
    function move(step) {
        const element = track.current;
        const next = Math.min(options.length - 1, Math.max(0, current + step));
        if (!element || next === current) return;
        element.scrollTo({left: next * element.clientWidth,
            behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
    }
    function open(option) {
        onLesson?.(row.n, [{ ...row, selectedOption: option }]);
    }
    return <li className="lesson forecast-card">
        <button type="button" className="forecast-header" tabIndex={onLesson ? undefined : -1} onClick={() => open(options[current])} aria-haspopup="dialog">
            <strong>{row.n}-я пара</strong><span>{when}</span>
            <span className="forecast-chance">Шанс пары ≈ {row.slotProbability}%</span>
        </button>
        <div className="subject-pager" role="group" aria-label={'Варианты предмета для пары № ' + row.n}
            onKeyDown={event => {
                if (!multiple || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
                event.preventDefault(); event.stopPropagation(); move(event.key === 'ArrowLeft' ? -1 : 1);
            }}>
            <div className="subject-caption"><span>{row.possibleTransfer ? 'Возможен перенос' : 'Если пара будет'}</span>
                {multiple && <span aria-live="polite" aria-atomic="true">{current + 1} из {options.length}
                    <span className="screen-reader-only">. {options[current].subject || 'Другой предмет'}, {options[current].conditional_probability}%.</span>
                </span>}
            </div>
            <div className="subject-pages">
                {multiple && <button type="button" className="subject-step" tabIndex={onLesson ? undefined : -1} aria-label="Предыдущий предмет" aria-controls={trackId}
                    aria-disabled={current === 0} onClick={() => move(-1)}><ChevronLeft size={17} /></button>}
                <div className="subject-track" ref={track} id={trackId}
                    onScroll={e => {
                        const el = e.currentTarget;
                        if (!el.clientWidth) return;
                        const next = Math.min(options.length - 1, Math.max(0, Math.round(el.scrollLeft / el.clientWidth)));
                        setPosition(next);
                        clearTimeout(settleTimer.current);
                        settleTimer.current = setTimeout(() => {
                            if (next !== settled.current && el.getClientRects().length) haptic();
                            settled.current = next;
                        }, 100);
                    }}>
                    {options.map((option, index) => <button key={option.subject} type="button" className="subject-slide"
                        aria-hidden={index !== current} tabIndex={onLesson && index === current ? 0 : -1} aria-haspopup="dialog"
                        onClick={() => open(option)}>
                        <strong>{option.subject ? shortSubjectName(option.subject) : 'Другой предмет'}</strong>
                        <span>≈ {option.conditional_probability}%</span>
                    </button>)}
                </div>
                {multiple && <button type="button" className="subject-step" tabIndex={onLesson ? undefined : -1} aria-label="Следующий предмет" aria-controls={trackId}
                    aria-disabled={current === options.length - 1} onClick={() => move(1)}><ChevronRight size={17} /></button>}
            </div>
        </div>
    </li>;
}

export function LessonList({ iso, lessons, type, entity, now, onLesson }) {
    const groups = Array.from(new Set(lessons.map(item => item.n))).map(n => [n, lessons.filter(item => item.n === n)]);
    const timing = now !== undefined ? timeState(iso, lessons, now) : null;
    const active = timing?.kind === 'running' ? timing.event.n : null;
    const next = timing?.nextEvent?.n;
    const course = /Д\d{1,2}-([1-4])|-([1-4])(?=[А-ЯЁA-Z])/iu.exec(entity || '');
    const teacherFirst = type === 'group' && Number(course?.[1] || course?.[2]) > 1;
    return <ol className="lesson-list" key={iso}>
        {groups.map(([n, rows]) => {
            if (rows[0].options?.length) return <ForecastCard key={JSON.stringify([iso, entity, n])}
                iso={iso} row={rows[0]} onLesson={onLesson} />;
            const [from, until] = pairTimes(iso, n);
            const status = active === n ? ' is-active' : next === n ? ' is-next' : '';
            const names = rows.map(row => {
                if (!row.subject) return Number.isFinite(row.slotProbability) ? `шанс пары ${row.slotProbability}%` : 'предмет пока неизвестен';
                return Number.isFinite(row.probability) ? `${row.subject}, примерный шанс ${row.probability}%` : row.subject;
            });
            return <li key={n} className={'lesson' + status}>
                {onLesson && <button type="button" className="lesson-open" aria-haspopup="dialog"
                    aria-label={n + ' пара · ' + names.join(' / ')}
                    onClick={() => onLesson(n, rows)} />}
                <span className="lesson-time lesson-start" aria-label={from ? 'Начало ' + from : undefined}>{from || '—'}</span>
                <div className="lesson-card">
                    <span className="screen-reader-only">{n} пара</span>
                    {rows.map((row, index) => {
                        const teacher = row.teacher.join(' / ');
                        const group = row.groups.join(' · ');
                        const lead = (type === 'teacher' ? group : teacherFirst && teacher) || '';
                        const subject = row.subject ? shortSubjectName(row.subject) : `${n}-я пара`;
                        const detail = [lead ? subject : teacher, type !== 'teacher' && group].filter(Boolean).join(' · ');
                        return <div key={index} className="lesson-detail">
                            <strong className={lead ? undefined : 'lesson-subject'}>{lead || subject}</strong>
                            {detail && <span className={'lesson-note' + (lead ? ' lesson-subject' : '')}>{detail}</span>}
                            {Number.isFinite(row.probability) && <span className="forecast-chance">Шанс ≈ {row.probability}%</span>}
                            {!row.subject && Number.isFinite(row.slotProbability) && <span className="forecast-chance">Шанс пары ≈ {row.slotProbability}%</span>}
                            {!!row.rooms.length && <span className="room">
                                {row.rooms.length > 1 ? 'аудитории' : 'аудитория'} {row.rooms.join(', ')}
                            </span>}
                        </div>;
                    })}
                </div>
                <div className="lesson-clock">
                    <span className="lesson-time lesson-end" aria-label={until ? 'Окончание ' + until : undefined}>{until}</span>
                    {active === n && <span className="now-pill">Сейчас</span>}
                </div>
            </li>;
        })}
    </ol>;
}

export function LessonDetails({ lesson, onClose }) {
    const [copyStatus, setCopyStatus] = useState('');
    const [stamp, setStamp] = useState(lesson.n === 5 && !lesson.preliminary);
    useEffect(() => {
        if (!stamp) return;
        const timer = setTimeout(() => setStamp(false), 1500);
        return () => clearTimeout(timer);
    }, [stamp]);
    const time = pairTimes(lesson.date, lesson.n).join('–');
    const when = [dateLabel(lesson.date), time].filter(Boolean).join(' · ');
    const lines = lesson.rows.map(({ subject, teacher, rooms, groups, probability, slotProbability, daysPresent, sampleDays, selectedOption }) => ({
        subject: selectedOption ? selectedOption.subject || 'Другой предмет' : subject,
        probability, slotProbability, daysPresent, sampleDays, selectedOption,
        info: [teacher.join(' / '), rooms.join(', '), groups.join(', ')].filter(Boolean).join(' · '),
    }));
    async function copy(event) {
        const trigger = event.currentTarget;
        try {
            await navigator.clipboard.writeText([when, ...lines.map(({ subject, info }) => [subject, info].filter(Boolean).join(' · '))].join('\n'));
            setCopyStatus('Скопировано');
            haptic('success');
            if (!matchMedia('(prefers-reduced-motion: reduce)').matches) window.launchReactionBurst?.(trigger, '✨');
        } catch {
            haptic('error');
            setCopyStatus('Не удалось скопировать');
        }
    }
    return <Sheet title={lesson.n + ' пара'} badge={stamp && <span className="fifth-stamp">Босс-файт</span>} onClose={onClose}>
        <div className="lesson-sheet">
            <p className="lesson-note">{when}</p>
            {lesson.preliminary && <p className="lesson-note">Предварительно</p>}
            {lines.map(({ subject, info, probability, slotProbability, daysPresent, sampleDays, selectedOption }, index) => <div key={index}><strong>{subject || 'Предмет пока неизвестен'}</strong>
                {info && <span className="lesson-note">{info}</span>}
                {selectedOption && <span className="lesson-note">Если пара будет, шанс этого варианта ≈ {selectedOption.conditional_probability}%.</span>}
                {!selectedOption && Number.isFinite(probability) && <span className="lesson-note">Примерный шанс — {probability}%</span>}
                {(selectedOption || !subject) && Number.isFinite(slotProbability) && <span className="lesson-note">Шанс пары ≈ {slotProbability}%</span>}
                {(selectedOption || !subject) && Number.isInteger(daysPresent) && <span className="lesson-note">В архиве эта пара была в {daysPresent} случаях из {sampleDays}.</span>}
            </div>)}
            {!lesson.preliminary && <button type="button" className="soft-button" onClick={copy}><Copy size={16} /> Скопировать пару</button>}
            {copyStatus && <p className="hint" role="status">{copyStatus}</p>}
        </div>
    </Sheet>;
}

export function PdfLink({ iso, type, entity }) {
    if (!entity) return null;
    return <a className="soft-button" href={'/api/schedule/download/groups/' + apiDate(iso)}
        target="_blank" rel="noopener" data-download-entity-type={type} data-download-entity-id={entity}><Download size={16} /> PDF</a>;
}

export function RouteLink({ href, icon: Icon, children, onNavigate }) {
    function open(event) {
        haptic('light');
        if (!onNavigate || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        onNavigate(() => location.assign(href));
    }
    return <a className="route-link" href={href} onClick={open}>
        {Icon && <Icon size={20} aria-hidden="true" />}<span>{children}</span><ArrowUpRight size={16} aria-hidden="true" />
    </a>;
}

export function openBot() {
    if (tg?.openTelegramLink) tg.openTelegramLink('https://t.me/kkepik_bot');
    else location.assign('https://t.me/kkepik_bot');
}
