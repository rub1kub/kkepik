import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { ChevronLeft, ChevronRight, Compass, Star, Users } from 'lucide-preact';
import { CatalogControls } from './catalog';
import { FavoriteRow, useFavoriteChange } from './favorites';
import { canWarmSchedules, haptic, loadSchedule, scheduleKey, studyDay, useQuery, warmSchedule } from './data';
import { parseLessons } from './schedule';
import { DayPager } from './day-pager';
import { DateHeader, Empty, IconButton, LessonDetails, LessonList, PdfLink, ScheduleSkeleton } from './ui';

const valueOf = item => ({ entity_type: item.type, entity_id: item.name, entity_name: item.name });

export function SearchPage({ today, now, visible, favorites, profile }) {
    const [type, setType] = useState('group');
    const [text, setText] = useState('');
    const [selected, setSelected] = useState(null);
    const [date, setDate] = useState(today);
    const [openedLesson, setOpenedLesson] = useState(null);
    const { change: changeFavorite, busy: saving, error: favoriteError, clearError } = useFavoriteChange();
    const results = useRef(null);
    const previousSearch = useRef(null);
    const selectedKey = selected ? scheduleKey(date, selected.type, selected.name) : null;
    const [schedule, error, retry] = useQuery(visible ? selectedKey : null,
        () => loadSchedule(date, selected.type, selected.name), { ttl: 45000 });
    const lessons = useMemo(() => parseLessons(schedule?.schedule, selected?.type), [schedule, selected]);
    const saved = selected && favorites.find(item => item.entity_type === selected.type && item.entity_id === selected.name);

    useEffect(() => {
        if (!visible || !schedule || schedule.unavailable || schedule.holiday) return;
        window.kkepikActivity?.shown(schedule, date, lessons.length ? 'view' : 'empty');
    }, [visible, schedule, date, lessons.length]);
    useEffect(() => {
        if (!visible || !selected || !schedule || !canWarmSchedules()) return;
        const timer = setTimeout(() => {
            for (const delta of [-1, 1]) warmSchedule(studyDay(date, delta), selected.type, selected.name).catch(() => {});
        }, 250);
        return () => clearTimeout(timer);
    }, [visible, selected, date, !!schedule]);

    useEffect(() => {
        if (!selected || !results.current) return;
        const top = results.current.getBoundingClientRect().top;
        if (top > (window.visualViewport?.height || innerHeight) * .45) results.current.scrollIntoView({ block:'start' });
    }, [selected]);
    function returnToResults() {
        haptic('soft');
        const previous = previousSearch.current;
        setSelected(null);
        setText(previous?.text || '');
        clearError();
        requestAnimationFrame(() => window.scrollTo(0, previous?.y || 0));
    }
    useEffect(() => {
        if (!visible || !selected) return;
        function back(event) { event.preventDefault(); returnToResults(); }
        window.addEventListener('kkepik:search-back', back);
        return () => window.removeEventListener('kkepik:search-back', back);
    }, [visible, selected]);
    function choose(name, nextType = type, isFavorite = false) {
        const value = name.trim();
        if (!value) return;
        previousSearch.current = { text, y:window.scrollY };
        const entry = { type: nextType, name: value };
        setType(nextType); setText(value); setSelected(entry); clearError();
        window.kkepikActivity?.selected(valueOf(entry), isFavorite);
        haptic();
    }
    function changeType(value) {
        if (value === type) return;
        haptic(); setType(value); setText(''); setSelected(null); clearError();
    }
    function chooseDay(next) {
        if (next === date) return;
        haptic();
        setDate(next);
    }
    return <div className="search-page">
        <header className="section-heading"><span className="section-mark"><Compass size={21} aria-hidden="true" /></span><h1>Поиск</h1></header>
        <CatalogControls type={type} text={text} visible={visible} showSuggestions={!selected} onType={changeType}
            onEdit={value => { setText(value); setSelected(null); clearError(); }} onPick={choose} />
        {!selected && !text.trim() && <>
            {!!favorites.length && <section className="search-saved">
                <h2>Избранное</h2>
                <div className="search-results">
                    {favorites.map(item => <FavoriteRow key={item.entity_type + ':' + item.entity_id} item={item} busy={saving}
                        onChoose={() => choose(item.entity_id, item.entity_type, true)} onRemove={() => changeFavorite('remove', item)} />)}
                </div>
                {favoriteError && <p className="form-error" role="alert">{favoriteError}</p>}
            </section>}
            {!favorites.length && profile?.name_or_group && <div className="search-results">
                <button className="choice-row" onClick={() => choose(profile.name_or_group, profile.role === 'teacher' ? 'teacher' : 'group')}>
                    <span className="catalog-icon"><Users size={17} aria-hidden="true" /></span><span>{profile.name_or_group}</span><ChevronRight size={17} />
                </button>
            </div>}
        </>}
        {selected && <section ref={results} className="search-schedule" key={selected.type + selected.name}>
            <div className="search-heading"><IconButton icon={ChevronLeft} label="К результатам поиска" onClick={returnToResults} /><h2>{selected.name}</h2>
                {selected.type !== 'audience' && <IconButton icon={Star} label={saved ? 'Убрать из избранного' : 'Добавить в избранное'} aria-pressed={!!saved}
                    className={saved ? 'star-on' : ''} disabled={saving}
                    onClick={() => changeFavorite(saved ? 'remove' : 'add', saved || valueOf(selected))} />}
            </div>
            {favoriteError && <p className="form-error" role="status">{favoriteError}</p>}
            <DateHeader iso={date} label={date === today ? 'Сегодня' : undefined}
                onDate={chooseDay} onPrev={() => chooseDay(studyDay(date, -1))} onNext={() => chooseDay(studyDay(date, 1))}
                onToday={date !== today ? () => chooseDay(today) : null} />
            <DayPager iso={date} type={selected.type} entity={selected.name} role={selected.type} enabled={visible} onDay={delta => chooseDay(studyDay(date, delta))}>
            {!schedule && !error && <ScheduleSkeleton count={2} />}
            {!schedule && error && <Empty title="Не удалось загрузить расписание" retry={retry}>{error.message}</Empty>}
            {schedule && (lessons.length ?
                <><LessonList iso={date} lessons={lessons} type={selected.type} entity={selected.name}
                    now={date === today ? now : undefined}
                    onLesson={(n, rows) => { haptic(n === 5 ? 'medium' : 'soft'); setOpenedLesson({ date, n, rows }); }} />
                    <div className="schedule-footer"><PdfLink iso={date} type={selected.type} entity={selected.name} /></div></> :
                <Empty title={schedule.unavailable ? 'Расписание пока недоступно' : 'Пар нет'} />)}
            </DayPager>
        </section>}
        {openedLesson?.date === date && visible && <LessonDetails lesson={openedLesson} onClose={() => setOpenedLesson(null)} />}
    </div>;
}
