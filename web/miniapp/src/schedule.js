const weekdayTimes = [
    ['08:45', '10:05'], ['10:25', '11:45'], ['12:05', '13:25'],
    ['13:35', '14:55'], ['15:05', '16:25'], ['16:35', '17:55'],
];
const saturdayTimes = [
    ['08:45', '10:00'], ['10:10', '11:25'], ['11:35', '12:50'],
    ['13:00', '14:15'], ['14:25', '15:40'], ['15:50', '17:05'],
];

export function pairTimes(iso, n) {
    const saturday = globalThis.kkepikCalendar?.isSaturday(iso) ||
        new Date(iso + 'T12:00:00+03:00').getUTCDay() === 6;
    return (saturday ? saturdayTimes : weekdayTimes)[n - 1] || [];
}

const entities = { '&amp;': '&', '&quot;': '"', '&#39;': "'", '&lt;': '<', '&gt;': '>', '&nbsp;': ' ' };
const plainText = value => String(value)
    .replace(/<\/?(?:b|i|em|strong|u|s|code|span)(?:\s[^<>]*)?>/gi, '')
    .replace(/&(amp|quot|#39|lt|gt|nbsp);/g, entity => entities[entity]);

const subjectShortNames = new Map([
    ['физическая культура', 'Физкультура'],
    ['иностранный язык в профессиональной деятельности', 'Проф. иностранный язык'],
    ['информационные технологии', 'Инф. технологии'],
    ['основы безопасности и защиты родины', 'ОБЗР'],
    ['безопасность жизнедеятельности', 'БЖД'],
    ['элементы высшей математики', 'Высшая математика'],
    ['основы алгоритмизации и программирования', 'Алгоритмы и программирование'],
    ['операционные системы и среды', 'ОС и среды'],
    ['основы финансовой грамотности', 'Финансовая грамотность'],
    ['графический дизайн и мультимедиа', 'Дизайн и мультимедиа'],
    ['проектирование и разработка интерфейсов пользователя', 'Проектирование интерфейсов'],
    ['стандартизация, сертификация и техническое документоведение', 'Стандартизация и сертификация'],
    ['технология разработки и защиты баз данных', 'Разработка и защита БД'],
    ['практика по администрированию и защите баз данных', 'Администрирование БД (практика)'],
    ['разработка прикладных приложений', 'Прикладная разработка'],
    ['эксплуатация серверных операционных систем', 'Эксплуатация серверных ОС'],
    ['администрирование сетевых операционных систем', 'Администрирование сетевых ОС'],
    ['разработка и администрирование информационной базы системы "1с:предприятие"', 'Разработка и администрирование 1С'],
    ['программирование на платформе "1с:предприятие"', 'Программирование на 1С'],
]);

export function shortSubjectName(subject) {
    return subject.split(' / ').map(part => {
        const name = part.trim();
        const key = name.replace(/\s+/g, ' ').replace(/[«»“”]/g, '"').toLowerCase();
        return subjectShortNames.get(key) || name
            .replace(/программно(?:е обеспечение|го обеспечения)(?!\p{L})/giu, 'ПО')
            .replace(/информационн(?:ых систем|ые системы)(?!\p{L})/giu, 'ИС')
            .replace(/баз[ыа]? данных(?!\p{L})/giu, 'БД');
    }).join(' / ');
}

const isRoom = value => value.replace(/^ауд(?:итория)?[.\s]+/i, '').split(/\s*[,/]\s*(?=\d)/).every(token => (
    /^(?:\d{1,4}(?:[-_][\p{L}\d]+)?|вц[-_ ]?\d+|с\s*\/\s*з(?:[_ -]?\d+)?|тир|иц|акт\.?\s*зал|спортзал|дист\.?|0[_/-]0)$/iu.test(token.trim())
));
const isTeacher = value => /[\p{L}-]+\s+\p{Lu}\.?\s*\p{Lu}\.?/u.test(value);

export function parseLessons(lines, type = 'group') {
    if (!Array.isArray(lines)) return [];
    const lessons = [];
    for (const item of lines.flatMap(line => String(line ?? '').split('\n'))) {
        const text = plainText(item).trim();
        const match = /^[▪▫•]?\uFE0F?\s*(\d+)\s*пара(?:\s*\([^)]*\))?\s*[–—-]\s*(.*)$/iu.exec(text);
        if (!match || !match[2] || /^нет(?:\s+пары)?$/iu.test(match[2].trim())) continue;
        const parts = match[2].split(/\s+[–—-]\s+/u).map(part => part.trim()).filter(Boolean);
        if (!parts.length) continue;
        const lesson = { n: parseInt(match[1], 10), subject: '', teacher: [], rooms: [], groups: [] };
        if (type === 'audience' || (type === 'teacher' && /^\d{2,3}[-‐‑–—]/.test(parts[0]))) lesson.groups.push(parts.shift());
        const subject = [];
        for (const part of parts) {
            if (subject.length && isRoom(part) && type !== 'audience') lesson.rooms.push(part.replace(/^ауд(?:итория)?[.\s]+/i, ''));
            else if (subject.length && isTeacher(part) && type !== 'teacher') lesson.teacher.push(part);
            else subject.push(part);
        }
        lesson.subject = subject.join(' — ') || 'Занятие';
        lessons.push(lesson);
    }
    return lessons.sort((a, b) => a.n - b.n);
}

export function timeState(iso, lessons, time = Date.now()) {
    if (!lessons.length) return null;
    const events = [...new Set(lessons.map(lesson => lesson.n))].map(n => {
        const [start, end] = pairTimes(iso, n);
        return start && { n, start, end, from: +new Date(iso + 'T' + start + ':00+03:00'), to: +new Date(iso + 'T' + end + ':00+03:00') };
    }).filter(Boolean);
    if (!events.length) return null;
    const running = events.find(event => time >= event.from && time < event.to);
    const next = events.find(event => time < event.from);
    if (running) return { event: running, nextEvent: next, kind: 'running', minutes: Math.ceil((running.to - time) / 60000), progress: (time - running.from) / (running.to - running.from) };
    if (next) {
        const previous = [...events].reverse().find(event => event.to <= time);
        return { event: next, nextEvent: next, kind: 'next', minutes: Math.ceil((next.from - time) / 60000), progress: previous ? (time - previous.to) / (next.from - previous.to) : 0 };
    }
    return { kind: 'done', progress: 1 };
}

export function homeScheduleDay(iso, nextStudyDay, lessons, published, dayOff, time) {
    if (dayOff) return nextStudyDay;
    if (published) return !lessons.length || timeState(iso, lessons, time)?.kind === 'done' ? nextStudyDay : iso;
    const [, end] = pairTimes(iso, weekdayTimes.length);
    return time >= +new Date(iso + 'T' + end + ':00+03:00') ? nextStudyDay : iso;
}

export const progressFormats = ['natural', 'digital', 'seconds', 'hours-minutes', 'decimal-hours', 'target-time'];

const moscowClock = new Intl.DateTimeFormat('en-GB', {
    timeZone:'Europe/Moscow', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23',
});
const moscowDate = new Intl.DateTimeFormat('ru-RU', {
    timeZone:'Europe/Moscow', day:'numeric', month:'long',
});
const russianUnits = Object.fromEntries(['hour', 'minute', 'second'].map(unit => [
    unit, new Intl.NumberFormat('ru-RU', { style:'unit', unit, unitDisplay:'long' }),
]));
const twoDigits = value => String(value).padStart(2, '0');

export function progressDisplay(format, state, time, rest, loading) {
    const event = state?.event;
    const running = state?.kind === 'running';
    if (format === 'natural' && !event) {
        if (rest) return { info:'Приятного', label:'отдыха!' };
        if (loading) return { info:'—', label:'до пары' };
        return { info:'Расписания пока нет' };
    }

    const clock = event ? '' : moscowClock.format(time);
    const seconds = event ? Math.max(0, Math.ceil(((running ? event.to : event.from) - time) / 1000)) :
        clock.split(':').reduce((total, part) => total * 60 + Number(part), 0);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const label = event ? (running ? 'до конца пары' : 'до пары') : 'сейчас';
    switch (format) {
        case 'digital':
            return { info:event ? twoDigits(minutes) + ':' + twoDigits(seconds % 60) : clock, label };
        case 'seconds':
            return { info:russianUnits.second.format(seconds), label:event ? label : 'с начала суток' };
        case 'hours-minutes':
            return { info:hours + ' ч ' + minutes % 60 + ' мин', label };
        case 'decimal-hours':
            return { info:(seconds / 3600).toFixed(2).replace('.', ',') + ' часа', label:event ? label : 'с начала суток' };
        case 'target-time':
            return event ? { info:running ? event.end : event.start, label:running ? 'конец пары' : 'начало пары' } :
                { info:clock.slice(0, 5), label:moscowDate.format(time) };
        default:
            if (minutes === 0) return { info:russianUnits.second.format(seconds), label };
            if (hours === 0) return { info:russianUnits.minute.format(minutes), label };
            const info = russianUnits.hour.format(hours);
            return { info:minutes % 60 ? info + ' и ' + russianUnits.minute.format(minutes % 60) : info, label };
    }
}
