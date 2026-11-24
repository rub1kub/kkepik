import test from 'node:test';
import assert from 'node:assert/strict';
import { homeScheduleDay, parseLessons, pairTimes, progressDisplay, progressFormats, shortSubjectName, timeState } from './schedule.js';

test('shortens known names and keeps both subjects and distinguishing terms', () => {
    assert.equal(shortSubjectName('Физическая культура / Высшая математика'), 'Физкультура / Высшая математика');
    assert.equal(shortSubjectName('Серверное программное обеспечение веб-приложений'), 'Серверное ПО веб-приложений');
    assert.equal(shortSubjectName('Тестирование информационных систем'), 'Тестирование ИС');
    assert.equal(shortSubjectName('Работа в информационных системах'), 'Работа в информационных системах');
    assert.equal(shortSubjectName('Иностранный язык'), 'Иностранный язык');
    assert.equal(shortSubjectName('Программирование на платформе «1С:Предприятие»'), 'Программирование на 1С');
});

test('home switches after the last Saturday class, with a late fallback when the schedule is missing', () => {
    const saturday = '2026-11-21';
    const monday = '2026-11-23';
    const lessons = [{ n: 1 }, { n: 3 }];
    const at = time => Date.parse(saturday + 'T' + time + '+03:00');
    assert.equal(homeScheduleDay(saturday, monday, lessons, true, false, at('12:49:59')), saturday);
    assert.equal(homeScheduleDay(saturday, monday, lessons, true, false, at('12:50:00')), monday);
    assert.equal(homeScheduleDay(saturday, monday, [], false, false, at('16:59:00')), saturday);
    assert.equal(homeScheduleDay(saturday, monday, [], false, false, at('17:05:00')), monday);
    assert.equal(homeScheduleDay(saturday, monday, [], true, false, at('08:00:00')), monday);
    assert.equal(homeScheduleDay(saturday, monday, [], false, true, at('08:00:00')), monday);
});

test('keeps subgroup lessons and skips empty pairs', () => {
    const rows = parseLessons(['▪️1 пара – Нет', '▪️2 пара – Физика – <b>Шульга Н.Н.</b> – 27\n▪️2 пара – История – Бороденко А.В. – 35']);
    assert.deepEqual(rows.map(item => [item.n,item.subject,item.teacher[0],item.rooms[0]]),
        [[2,'Физика','Шульга Н.Н.','27'],[2,'История','Бороденко А.В.','35']]);
});

test('parses group and room in a teacher schedule and the standalone format', () => {
    const rows = parseLessons(['▪️1 пара – 103-Д9-3ИНС – Физика – ауд. 27', '▪️2 пара – Физика – 27'], 'teacher');
    assert.deepEqual(rows.map(item => [item.subject,item.groups,item.rooms]),
        [['Физика',['103-Д9-3ИНС'],['27']],['Физика',[],['27']]]);
});

test('the timer uses the chosen schedule and Saturday bells in Moscow', () => {
    const date = '2026-11-21';
    const lessons = [{n:2},{n:4}];
    assert.deepEqual(pairTimes(date,2), ['10:10','11:25']);
    const running = timeState(date,lessons,+new Date('2026-11-21T07:15:00Z'));
    assert.equal(running.kind,'running');
    assert.equal(running.event.n,2);
    assert.equal(running.nextEvent.n,4);
    const gap = timeState(date,lessons,+new Date('2026-11-21T09:30:00Z'));
    assert.equal(gap.event.n,4);
    assert.equal(gap.nextEvent.n,4);
    assert.equal(gap.minutes,30);
    assert.equal(timeState(date,lessons,+new Date('2026-11-21T12:00:00Z')).kind,'done');
});

test('all six formats use the active lesson end and the clock uses Moscow time', () => {
    const time = Date.parse('2026-11-21T09:00:05Z');
    const active = timeState('2026-11-21', [{ n:3 }], time);
    const values = progressFormats.map(format => progressDisplay(format, active, time, false, false).info);
    assert.deepEqual(values, ['49 минут', '49:55', '2\u00a0995 секунд', '0 ч 49 мин', '0,83 часа', '12:50']);
    assert.deepEqual(progressDisplay('digital', null, time, true, false), { info:'12:00:05', label:'сейчас' });
});
