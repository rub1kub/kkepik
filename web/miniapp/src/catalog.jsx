import { useMemo, useRef } from 'preact/hooks';
import { BookOpen, ChevronRight, DoorOpen, Search, Users, X } from 'lucide-preact';
import { api, catalogKind, useQuery } from './data';
import { IconButton, Skeleton } from './ui';

const modes = [
    { id: 'group', title: 'Группы', icon: Users, placeholder: 'Номер группы' },
    { id: 'teacher', title: 'Преподаватели', icon: BookOpen, placeholder: 'Фамилия преподавателя' },
    { id: 'audience', title: 'Аудитории', icon: DoorOpen, placeholder: 'Номер аудитории' },
];
const normalize = value => value.toLocaleLowerCase('ru').replaceAll('ё', 'е').replace(/[\s.‐‑–—-]/g, '');

export function CatalogControls({ type, text, visible, favoritesOnly, showSuggestions = true,
    busy, onType, onEdit, onPick }) {
    const input = useRef(null);
    const [catalog, error, retry] = useQuery(visible ? 'catalog' : null,
        () => api('/api/schedule/catalog'), { ttl: 300000 });
    const mode = modes.find(item => item.id === type);
    const names = useMemo(() => (catalog?.[catalogKind(type)] || []).map(name => ({ name, key:normalize(name) })), [type, catalog]);
    const suggestions = useMemo(() => {
        const needle = normalize(text.trim());
        if (!needle) return [];
        const found = [];
        for (const { name, key } of names) {
            const index = key.indexOf(needle);
            if (index !== -1) found.push({ name, index });
        }
        return found.sort((a, b) => a.index - b.index || a.name.localeCompare(b.name, 'ru', { numeric: true }))
            .slice(0, 9).map(item => item.name);
    }, [names, text]);

    function pick(name) {
        if (!name || busy) return;
        input.current?.blur();
        onPick(name);
    }

    return <div className="catalog-controls">
        <div className={'segments search-modes' + (favoritesOnly ? ' favorite-modes' : '')} aria-label="Что ищем">
            {modes.filter(item => !favoritesOnly || item.id !== 'audience').map(item =>
                <button key={item.id} type="button" disabled={busy} aria-pressed={type === item.id} onClick={() => onType(item.id)}>
                    {item.title}
                </button>)}
        </div>
        <form role="search" className="search-field" onSubmit={event => {
            event.preventDefault();
            const value = text.trim();
            if (value) pick(suggestions.find(name => normalize(name) === normalize(value)) ||
                suggestions[0] || (!favoritesOnly && value));
        }}>
            <Search size={20} aria-hidden="true" />
            <input ref={input} value={text} disabled={busy} placeholder={mode.placeholder} aria-label={mode.placeholder}
                autoComplete="off" enterKeyHint="search" onInput={event => onEdit(event.currentTarget.value)} />
            {text && <IconButton icon={X} label="Очистить поиск" disabled={busy}
                onClick={() => { onEdit(''); input.current?.focus(); }} />}
        </form>
        {showSuggestions && text.trim() && <>
            {!catalog && !error && <Skeleton count={2} />}
            {!catalog && error && <button className="soft-button" onClick={retry}>Повторить загрузку подсказок</button>}
            {!!suggestions.length && <div className="search-results">
                {suggestions.map(name => <button className="choice-row" type="button" key={name} disabled={busy} onClick={() => pick(name)}>
                    <span className="catalog-icon"><mode.icon size={17} aria-hidden="true" /></span><span>{name}</span>
                    <ChevronRight size={16} aria-hidden="true" />
                </button>)}
            </div>}
            {catalog && !suggestions.length && <p className="hint">Совпадений нет. Попробуй изменить запрос.</p>}
        </>}
    </div>;
}
