import { render } from 'preact';
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { ClipboardList, Gamepad2, Grid2X2, House, Search, Settings2 } from 'lucide-preact';
import { bootstrap, haptic, initData, tg, today as localToday, useQuery } from './data';
import { Home } from './home';
import { SearchPage } from './search';
import { Duties } from './duties';
import { RouteLink, Sheet } from './ui';
import './style.css';
import './campaign.css';

const tabs = [
    { id: 'home', label: 'Главная', icon: House },
    { id: 'search', label: 'Поиск', icon: Search },
    { id: 'duty', label: 'Дежурство', icon: ClipboardList },
];
function currentTab() {
    const tab = new URLSearchParams(location.search).get('tab');
    return tabs.some(item => item.id === tab) ? tab : 'home';
}
const signedUser = (() => {
    try { return JSON.parse(new URLSearchParams(initData).get('user') || '{}'); } catch { return {}; }
})();

function HapticSetting() {
    const feedback = window.kkepikHaptics;
    const [enabled, setEnabled] = useState(feedback?.enabled);
    if (!feedback?.supported) return null;
    return <label className="setting-row"><span>Тактильный отклик<br /><small>Расписание и дежурства</small></span>
        <input type="checkbox" role="switch" checked={enabled} onChange={event => {
            const on = event.currentTarget.checked;
            feedback.setEnabled(on);
            setEnabled(on);
            if (on) haptic('light');
        }} /></label>;
}

function App() {
    const [tab, setTab] = useState(currentTab);
    const [profile, setProfile] = useState(false);
    const [ready, setReady] = useState(null);
    const [error, setError] = useState(null);
    const [retry, setRetry] = useState(0);
    const [now, setNow] = useState(Date.now());
    const [online, setOnline] = useState(navigator.onLine);
    const [favorites] = useQuery('favorites', () => Promise.resolve([]), { ttl: Infinity });
    const [opened, setOpened] = useState(() => new Set([currentTab()]));
    const today = localToday();
    const initialDate = useRef(today);
    const needsBootstrap = opened.has('home') || opened.has('search');
    const scrolls = useRef({});
    const tabRef = useRef(tab);
    const indicator = useRef(null);
    const indicatorOrigin = useRef(null);
    const scrollTarget = useRef(null);

    useLayoutEffect(() => {
        if (!needsBootstrap) return;
        let active = true;
        setError(null);
        bootstrap(initialDate.current).then(data => {
            if (!active) return;
            setReady(data);
        }).catch(problem => { if (active) setError(problem); });
        return () => { active = false; };
    }, [retry, needsBootstrap]);
    useEffect(() => {
        function syncTime() { setNow(Date.now()); }
        function syncNetwork() { setOnline(navigator.onLine); }
        const interval = setInterval(syncTime, 10000);
        document.addEventListener('visibilitychange', syncTime);
        tg?.onEvent?.('activated', syncTime);
        window.addEventListener('online', syncNetwork);
        window.addEventListener('offline', syncNetwork);
        return () => {
            clearInterval(interval);
            document.removeEventListener('visibilitychange', syncTime);
            tg?.offEvent?.('activated', syncTime);
            window.removeEventListener('online', syncNetwork);
            window.removeEventListener('offline', syncNetwork);
        };
    }, []);
    useEffect(() => {
        function applyTab(next) {
            const previous = tabRef.current;
            if (previous === next) {
                window.scrollTo(0, 0);
                return;
            }
            if (previous !== 'home') scrolls.current[previous] = scrollY;
            scrollTarget.current = next === 'home' ? 0 : scrolls.current[next] || 0;
            indicatorOrigin.current = indicator.current?.getBoundingClientRect();
            tabRef.current = next;
            setOpened(values => new Set([...values, next]));
            setTab(next);
            setProfile(false);
        }
        function handleHistory() { applyTab(currentTab()); }
        function handleTab(event) { applyTab(event.detail); }
        window.addEventListener('popstate', handleHistory);
        window.addEventListener('kkepik:tab', handleTab);
        return () => {
            window.removeEventListener('popstate', handleHistory);
            window.removeEventListener('kkepik:tab', handleTab);
        };
    }, []);
    useLayoutEffect(() => {
        const origin = indicatorOrigin.current;
        indicatorOrigin.current = null;
        let movement;
        if (origin && indicator.current && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
            const target = indicator.current.getBoundingClientRect();
            movement = indicator.current.animate([
                { transform:'translate(' + (origin.left - target.left) + 'px,' + (origin.top - target.top) + 'px)' },
                { transform:'none' }
            ], { duration:210, easing:'cubic-bezier(.22,.8,.26,1)' });
        }
        if (scrollTarget.current !== null) {
            window.scrollTo(0, scrollTarget.current);
            scrollTarget.current = null;
        }
        return () => movement?.cancel();
    }, [tab]);
    useEffect(() => {
        const button = tg?.BackButton;
        const settings = tg?.isVersionAtLeast?.('7.0') ? tg.SettingsButton : null;
        const activeSheet = () => document.querySelector('dialog.sheet[open]');
        function updateBack() {
            if (activeSheet()) settings?.hide?.(); else settings?.show?.();
            if (activeSheet() || tab !== 'home') button?.show?.(); else button?.hide?.();
        }
        function back() {
            const sheet = activeSheet();
            if (sheet) sheet.dispatchEvent(new Event('cancel', { cancelable:true }));
            else if (tab !== 'search' || window.dispatchEvent(new Event('kkepik:search-back', { cancelable:true }))) navigate('home', true);
        }
        updateBack();
        window.addEventListener('kkepik:sheet', updateBack);
        button?.onClick?.(back);
        settings?.onClick?.(openProfile);
        return () => {
            window.removeEventListener('kkepik:sheet', updateBack);
            button?.offClick?.(back);
            settings?.offClick?.(openProfile);
        };
    }, [tab]);

    function openProfile() {
        if (document.querySelector('dialog[open]')) return;
        haptic('soft');
        setProfile(true);
    }
    function navigate(next, replace = false) {
        if (next !== tabRef.current) {
            const url = new URL(location.href);
            if (next === 'home') url.searchParams.delete('tab'); else url.searchParams.set('tab', next);
            history[replace ? 'replaceState' : 'pushState'](null, '', url);
            haptic();
            window.kkepikActivity?.track('ui.action', { details: { control: 'miniapp_tab_' + next } });
        }
        window.dispatchEvent(new CustomEvent('kkepik:tab', { detail: next }));
    }

    return <div className="app-shell" data-tab={tab}>
        {!online && <div className="network-note" role="status">Нет сети. Доступны сохранённые данные.</div>}
        <main className="app-content" id="main-content">
            {opened.has('home') && <div className="tab-pane" hidden={tab !== 'home'}>
                <Home now={now} today={today} boot={ready} error={error} retry={() => setRetry(n => n + 1)} favorites={favorites || []} visible={tab === 'home'} />
                <footer className="home-footer">
                    <button className="settings-link" type="button" onClick={openProfile}>
                        <Settings2 size={16} aria-hidden="true" /> Настройки
                    </button>
                </footer>
            </div>}
            {opened.has('search') && <div className="tab-pane" hidden={tab !== 'search'}>
                <SearchPage today={today} now={now} visible={tab === 'search'} favorites={favorites || []} profile={ready?.user} />
            </div>}
            {opened.has('duty') && <div className="tab-pane" hidden={tab !== 'duty'}>
                <Duties today={today} now={now} visible={tab === 'duty'} />
            </div>}
        </main>
        <nav className="tab-bar" aria-label="Разделы мини-приложения">
            {tabs.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-current={tab === id ? 'page' : undefined}
                data-tab={id} onClick={() => navigate(id)}><span className="tab-icon">
                    {tab === id && <span className="tab-indicator" ref={indicator} aria-hidden="true" />}
                    <Icon size={21} aria-hidden="true" />
                </span><span>{label}</span></button>)}
        </nav>
        {profile && <Sheet title="Настройки" onClose={() => setProfile(false)}>{dismiss => <>
            {signedUser.first_name && <div className="profile-summary"><strong>{[signedUser.first_name, signedUser.last_name].filter(Boolean).join(' ')}</strong><span>{ready?.user?.name_or_group}</span></div>}
            <div className="sheet-list">
                <HapticSetting />
                <RouteLink href="/settings" icon={Settings2} onNavigate={dismiss}>Настройки расписания</RouteLink>
                <RouteLink href="/mini/classic" icon={Grid2X2} onNavigate={dismiss}>Архив и другие разделы</RouteLink>
                <RouteLink href="/games/snake" icon={Gamepad2} onNavigate={dismiss}>Змейка</RouteLink>
            </div>
        </>}</Sheet>}
    </div>;
}

if (initData) {
    const root = document.getElementById('miniapp-root');
    function setTheme() {
        const theme = tg.colorScheme === 'light' ? 'light' : 'dark';
        document.documentElement.dataset.theme = theme;
        tg.setBackgroundColor?.('bg_color');
        tg.setHeaderColor?.('bg_color');
        tg.setBottomBarColor?.('bottom_bar_bg_color');
    }
    setTheme(); tg.onEvent?.('themeChanged', setTheme);
    tg.expand?.();
    tg.ready?.();
    const startup = window.kkepikMiniappStartup;
    Promise.resolve(startup?.styles ?? true).then(stylesReady => {
        if (!stylesReady) { startup?.fail(); return; }
        render(<App />, root);
        startup?.finish();
    }).catch(() => startup?.fail());
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js', { updateViaCache: 'none' }).catch(() => {}), { once: true });
    }
}
