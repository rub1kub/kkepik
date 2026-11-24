import { useRef, useState } from 'preact/hooks';
import { Check, ChevronRight, Star, Trash2 } from 'lucide-preact';
import { ApiError, haptic, saveFavorite } from './data';
import { IconButton } from './ui';

export function useFavoriteChange() {
    const pending = useRef(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    async function change(action, item) {
        if (pending.current) return false;
        pending.current = true;
        setBusy(true);
        setError('');
        try {
            await saveFavorite(action, item);
            haptic('success');
            return true;
        } catch (problem) {
            haptic('error');
            setError(problem instanceof ApiError ? problem.message : 'Не удалось сохранить изменения. Попробуйте ещё раз.');
            return false;
        } finally {
            pending.current = false;
            setBusy(false);
        }
    }

    return { change, busy, error, clearError: () => setError('') };
}

export function FavoriteRow({ item, selected, busy, onChoose, onRemove }) {
    const name = item.entity_name;
    return <div className="favorite-entry" data-selected={selected || undefined}>
        <button type="button" className="choice-row" aria-pressed={selected} onClick={onChoose}>
            <span className="catalog-icon"><Star size={17} aria-hidden="true" /></span><span>{name}</span>
            {selected && <Check size={17} aria-hidden="true" />}
            {selected === undefined && <ChevronRight size={17} aria-hidden="true" />}
        </button>
        <IconButton icon={Trash2} label={'Удалить из избранного — ' + name} className="favorite-remove"
            disabled={busy} onClick={onRemove} />
    </div>;
}
