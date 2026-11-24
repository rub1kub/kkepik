import { useLayoutEffect, useRef, useState } from 'preact/hooks';
import { peek, predictionKey, scheduleKey, studyDay, today } from './data';
import { parseLessons } from './schedule';
import { Empty, ForecastSchedule, LessonList, PracticeForecastPause, ScheduleSkeleton } from './ui';

const translate = x => 'translate3d(' + x + 'px,0,0)';
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// Read already loaded data so dragging never starts a request or records another schedule view.
function DayPreview({ iso, type, entity, role, name, step }) {
    const day = studyDay(iso, step);
    const cached = peek(scheduleKey(day, type, entity));
    const lessons = parseLessons(cached?.schedule, role);
    const group = name || cached?.[role] || entity;
    const forecast = cached?.unavailable && name && role === 'group' ? peek(predictionKey(day, name)) : null;
    let content = <ScheduleSkeleton />;
    if (lessons.length) content = <LessonList iso={day} lessons={lessons} type={role} entity={group}
        now={day === today() ? Date.now() : undefined} />;
    else if (forecast?.status === 'preliminary') content = <ForecastSchedule iso={day} group={group} forecast={forecast} />;
    else if (forecast?.reason === 'practice') content = <PracticeForecastPause />;
    else if (cached?.schedule) content = <Empty title="Пар нет" />;
    else if (cached?.unavailable && (!name || forecast)) content = <Empty title="Расписание пока недоступно" />;
    return <div className={'day-preview ' + (step > 0 ? 'after' : 'before')} aria-hidden="true" inert={true}>
        {content}
    </div>;
}

export function DayPager({ iso, type, entity, role, name, onDay, enabled = true, children }) {
    const viewport = useRef(null);
    const touch = useRef(null);
    const rail = useRef(null);
    const onDayRef = useRef(onDay);
    const committedDay = useRef(null);
    const previous = useRef({ iso, type, entity });
    const [side, setSide] = useState(0);
    onDayRef.current = onDay;

    useLayoutEffect(() => {
        const surface = viewport.current;
        const track = rail.current;
        const last = previous.current;
        const internal = committedDay.current === iso;
        previous.current = { iso, type, entity };
        committedDay.current = null;
        if (!internal || !enabled) touch.current = null;
        if (!enabled) return;
        let frame = 0;
        let motion = null;
        let finishSlide = null;

        function stop() {
            cancelAnimationFrame(frame);
            frame = 0;
            if (motion) { motion.onfinish = null; motion.cancel(); motion = null; }
            finishSlide = null;
        }
        function clear() {
            stop();
            track.style.removeProperty('transform');
            track.style.removeProperty('will-change');
            setSide(0);
        }
        if (!internal && iso !== last.iso && type === last.type && entity === last.entity && !reducedMotion()) {
            motion = track.animate([{ transform:translate(iso > last.iso ? 14 : -14) }, { transform:'none' }],
                { duration:155, easing:'ease-out' });
        }

        function settle(g, step) {
            const target = -step * g.width;
            if (step && g.motion) setSide(step);
            stop();
            const finish = () => {
                clear();
                if (step) {
                    committedDay.current = studyDay(iso, step);
                    onDayRef.current(step);
                }
            };
            if (!g.motion || Math.abs(target - g.painted) < 1) { finish(); return; }
            track.style.transform = translate(target);
            const distance = Math.min(1, Math.abs(target - g.painted) / g.width);
            motion = track.animate([{ transform:translate(g.painted) }, { transform:translate(target) }], {
                duration:120 + 110 * distance, easing:'cubic-bezier(.2,.72,.25,1)'
            });
            finishSlide = step ? finish : null;
            motion.onfinish = finish;
        }
        function cancel() {
            const g = touch.current;
            touch.current = null;
            if (g?.locked || g?.base) settle(g, 0);
        }
        function start(e) {
            if (e.touches.length !== 1) { cancel(); return; }
            if (e.target.closest('.subject-pager, details, dialog, a, button:not(.lesson-open):not(.forecast-header)')) return;
            // A second flick can start before the previous page has finished settling.
            if (finishSlide) finishSlide();
            const transform = motion && getComputedStyle(track).transform;
            const base = transform && transform !== 'none' ? new DOMMatrix(transform).m41 : 0;
            stop();
            if (base) track.style.transform = translate(base);
            else track.style.removeProperty('transform');
            const t = e.touches[0];
            touch.current = { x:t.clientX, y:t.clientY, base, dx:base, painted:base, locked:false,
                width:track.clientWidth, motion:!reducedMotion() };
        }
        function move(e) {
            const g = touch.current;
            if (!g) return;
            if (e.touches.length !== 1) { cancel(); return; }
            const t = e.touches[0];
            const dx = t.clientX - g.x, dy = t.clientY - g.y;
            if (!g.locked) {
                if (Math.max(Math.abs(dx), Math.abs(dy)) < 5) return;
                if (Math.abs(dx) <= Math.abs(dy) || !e.cancelable) {
                    touch.current = null;
                    if (g.base) settle(g, 0);
                    else clear();
                    return;
                }
                g.locked = true;
                if (g.motion) track.style.willChange = 'transform';
            }
            if (e.cancelable) e.preventDefault();
            g.dx = Math.max(-g.width, Math.min(g.width, g.base + dx));
            if (!g.motion) return;
            const direction = g.dx < 0 ? 1 : -1;
            if (g.side !== direction) { g.side = direction; setSide(direction); }
            if (!frame) frame = requestAnimationFrame(() => {
                frame = 0;
                g.painted = g.dx;
                track.style.transform = translate(g.dx);
            });
        }
        function end(e) {
            const g = touch.current;
            touch.current = null;
            if (!g?.locked) {
                if (g?.base) settle(g, 0);
                return;
            }
            if (e.cancelable) e.preventDefault();
            const dx = g.base + e.changedTouches[0].clientX - g.x;
            const step = Math.abs(dx) >= Math.min(70, g.width * .18) ? (dx < 0 ? 1 : -1) : 0;
            settle(g, step);
        }
        surface.addEventListener('touchstart', start, { passive:true });
        surface.addEventListener('touchmove', move, { passive:false });
        surface.addEventListener('touchend', end, { passive:false });
        surface.addEventListener('touchcancel', cancel);
        return () => {
            surface.removeEventListener('touchstart', start);
            surface.removeEventListener('touchmove', move);
            surface.removeEventListener('touchend', end);
            surface.removeEventListener('touchcancel', cancel);
            clear();
        };
    }, [iso, type, entity, enabled]);

    return <div ref={viewport} className="day-window">
        <div ref={rail} className="day-rail">
            <div className="day-page">{children}</div>
            {side !== 0 && <DayPreview iso={iso} type={type} role={role} name={name} entity={entity} step={side} />}
        </div>
    </div>;
}
