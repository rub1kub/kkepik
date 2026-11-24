from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from schedules.schedule_prediction import SchoolDay, history_for, predict, probability, schedule_prediction


class SchedulePredictionTest(TestCase):
    def test_matches_weekday_and_leaves_uncertain_subjects_unspecified(self):
        start = date(2026, 9, 7)
        history = [SchoolDay(start + timedelta(weeks=i), {1: ('Математика',), 2: (f'Предмет {i}',)})
                   for i in range(10)]
        target = start + timedelta(weeks=10)
        history.append(SchoolDay(target, {1: ('Еще не известный предмет',)}))
        result = predict(history, target)
        self.assertEqual(result['weekday_samples'], 10)
        self.assertEqual([{k: v for k, v in item.items() if k != 'subject_options'} for item in result['lessons']], [
            {'n': 1, 'subject': 'Математика', 'probability': 95, 'slot_probability': 95, 'days_present': 10},
            {'n': 2, 'subject': None, 'probability': None, 'slot_probability': 95, 'days_present': 10},
        ])
        options = result['lessons'][0]['subject_options']
        self.assertEqual(options, [{'subject': 'Математика', 'conditional_probability': 91},
                                   {'subject': None, 'conditional_probability': 9}])
        self.assertNotIn('Еще не известный предмет', [item['subject'] for item in options])

    def test_regular_slot_with_no_predictable_subject_still_has_a_forecast(self):
        start = date(2026, 9, 3)
        days = [SchoolDay(start + timedelta(weeks=i), {4: (f'Предмет {i}',)} if i else {}) for i in range(10)]
        result = predict(days, start + timedelta(weeks=10))
        self.assertEqual(result['weekday_samples'], 10)
        self.assertEqual(len(result['lessons']), 1)
        lesson = result['lessons'][0]
        self.assertEqual((lesson['n'], lesson['days_present']), (4, 9))
        self.assertIsNone(lesson['subject'])
        self.assertIsNone(lesson['probability'])
        self.assertGreaterEqual(lesson['slot_probability'], 75)
        options = lesson['subject_options']
        self.assertEqual(sum(option['conditional_probability'] for option in options), 100)
        self.assertTrue(all(option['conditional_probability'] > 0 for option in options))
        self.assertIsNone(options[-1]['subject'])
        self.assertEqual({item['subject'] for item in options[:-1]}, {f'Предмет {i}' for i in range(1, 10)})

    def test_alternating_weeks_can_outweigh_the_single_most_recent_day(self):
        start = date(2026, 3, 2)
        history = [SchoolDay(start + timedelta(weeks=i), {1: (('Математика', 'История')[i % 2],)})
                   for i in range(18)]
        result = predict(history, start + timedelta(weeks=18))
        self.assertEqual(len(result['lessons']), 1)
        self.assertEqual(result['lessons'][0]['subject'], 'Математика')

    def test_recent_subject_pattern_can_change_the_forecast(self):
        start = date(2026, 3, 2)
        rotation = (0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0)
        subjects = ('Математика', 'История')
        history = []
        for i, module in enumerate(rotation):
            day = start + timedelta(weeks=i)
            subject = subjects[module]
            history.extend(SchoolDay(day - timedelta(days=lag), {1: (subject + ' в модуле',)}) for lag in range(6, 1, -1))
            history.append(SchoolDay(day, {1: (subject,)}))

        target = start + timedelta(weeks=len(rotation))
        for subject in subjects:
            with self.subTest(subject=subject):
                context = [SchoolDay(target - timedelta(days=lag), {1: (subject + ' в модуле',)}) for lag in range(6, 1, -1)]
                result = predict(history + context, target)
                self.assertEqual(result['lessons'][0]['subject'], subject)

    def test_groups_spelling_variants_and_drops_old_history(self):
        start = date(2026, 9, 7)
        variants = ('Учет в «1С»', 'Учёт в "1С"')
        history = [SchoolDay(start + timedelta(weeks=i), {1: (variants[i % 2],)}) for i in range(8)]
        active = predict(history, start + timedelta(weeks=8))
        self.assertEqual(active['lessons'][0]['subject'], variants[-1])
        self.assertEqual(predict(history, start + timedelta(weeks=11)), {})

    def test_probability_includes_missing_slots_and_penalizes_misses_in_validation(self):
        consistent = 1.0
        sometimes_missing = 0.65
        self.assertLess(probability(sometimes_missing, 8, []), probability(consistent, 8, []))
        misses = [[(0.65, False)], [(0.68, False)]]
        self.assertLess(probability(sometimes_missing, 8, misses), probability(sometimes_missing, 8, []))

    def test_short_published_day_can_raise_chance_of_the_fourth_pair(self):
        start = date(2026, 8, 31)
        transfer_weeks = {6, 9, 12, 15}
        history = []
        for week in range(16):
            monday = start + timedelta(weeks=week)
            slots = {1: ('Математика',), 2: ('История',)}
            if week not in transfer_weeks:
                slots[3] = ('Численные методы',)
            history.append(SchoolDay(monday, slots))
            if week == 15:
                break
            slots = {1: ('Математика',), 2: ('Физика',), 3: ('История',)}
            if week in transfer_weeks:
                slots[4] = ('Численные методы',)
            history.append(SchoolDay(monday + timedelta(days=1), slots))

        target = start + timedelta(weeks=15, days=1)
        baseline = predict(history[:-1], target)
        self.assertEqual([lesson['n'] for lesson in baseline['lessons']], [1, 2, 3])
        result = predict(history, target)
        self.assertEqual([lesson['n'] for lesson in result['lessons']], [1, 2, 3, 4])
        fourth = result['lessons'][-1]
        self.assertGreater(fourth['slot_probability'], 50)
        self.assertTrue(fourth['possible_transfer'])
        self.assertEqual(fourth['subject_options'][0]['subject'], 'Численные методы')
        self.assertEqual(sum(option['conditional_probability'] for option in fourth['subject_options']), 100)

        # A fully cancelled or unpublished day cannot provide transfer evidence.
        result = predict(history[:-1] + [SchoolDay(target - timedelta(days=1), {})], target)
        self.assertEqual([lesson['n'] for lesson in result['lessons']], [1, 2, 3])
        weak_history = [SchoolDay(row.day, {n: subject for n, subject in row.slots.items() if n < 4
                                           or row.day == start + timedelta(weeks=6, days=1)}) for row in history]
        self.assertEqual([lesson['n'] for lesson in predict(weak_history, target)['lessons']], [1, 2, 3])

    def test_practice_pauses_from_first_day_without_resetting_each_day(self):
        start = date(2026, 9, 7)
        history = [SchoolDay(start + timedelta(weeks=i), {1: ('Математика',), 2: ('История',)})
                   for i in range(9)]
        practice = start + timedelta(weeks=9)
        mixed = SchoolDay(practice - timedelta(days=2), {1: ('Практика по сетям',), 2: ('Физика',), 3: ('История',)})
        self.assertIn('lessons', predict(history + [mixed], practice))
        history += [SchoolDay(practice + timedelta(days=i), {1: ('Практика по сетям',), 2: ('Учебная практика',)})
                    for i in (0, 1, 2, 4, 5, 7, 8, 12)]
        result = predict(history, practice + timedelta(days=13))
        self.assertEqual(result, {'status': 'unavailable', 'reason': 'practice',
                                  'resume_on': (practice + timedelta(days=14)).isoformat()})
        self.assertIn('lessons', predict(history, practice + timedelta(days=14)))

    def test_second_day_transfer_requires_a_published_day_without_the_subject(self):
        start = date(2026, 8, 31)
        regular = {1: ('Математика',), 2: ('История',), 3: ('Физика',)}
        abbreviated = {n: subject for n, subject in regular.items() if n < 3}
        steady = {1: ('Математика',), 2: ('История',), 3: ('Информатика',)}
        history = []
        for week in range(15):
            monday = start + timedelta(weeks=week)
            shortened = week in {6, 8, 12}
            history.extend([SchoolDay(monday, abbreviated if shortened else regular),
                            SchoolDay(monday + timedelta(days=1), steady),
                            SchoolDay(monday + timedelta(days=2), {**steady, 4: ('Физика',)} if shortened else steady)])

        monday = start + timedelta(weeks=15)
        tuesday = monday + timedelta(days=1)
        target = monday + timedelta(days=2)
        history.append(SchoolDay(monday, abbreviated))
        self.assertEqual(len(predict(history, target)['lessons']), 3)
        self.assertEqual(len(predict(history + [SchoolDay(tuesday, regular)], target)['lessons']), 3)
        forecast = predict(history + [SchoolDay(tuesday, steady)], target)
        self.assertEqual(forecast['lessons'][-1]['n'], 4)
        self.assertTrue(forecast['lessons'][-1]['possible_transfer'])

    def test_subject_that_disappears_loses_weight_but_a_return_restores_it(self):
        start = date(2026, 9, 7)
        target = start + timedelta(weeks=10)
        subject = 'Тестирование'
        history = []
        for week in range(10):
            monday = start + timedelta(weeks=week)
            for day in range(5):
                slots = {1: ((subject if week < 8 else 'Математика') if day == 0 else 'История',)}
                history.append(SchoolDay(monday + timedelta(days=day), slots))

        def chance(rows):
            options = predict(rows, target)['lessons'][0]['subject_options']
            return next((option['conditional_probability'] for option in options if option['subject'] == subject), 0)

        inactive_chance = chance(history)
        newest = history[-1]
        returned = history[:-1] + [SchoolDay(newest.day, {**newest.slots, 2: (subject,)})]
        self.assertLess(inactive_chance, 25)
        self.assertGreater(chance(returned), inactive_chance * 2)

    def test_latest_revision_empty_days_and_new_publication(self):
        group = '101-Д9'
        start = date(2026, 9, 7)
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'knowledge.db'
            with sqlite3.connect(path) as connection:
                connection.executescript("""
                    CREATE TABLE revisions(id INTEGER PRIMARY KEY, date TEXT, revision INTEGER, status TEXT, groups_json TEXT);
                    CREATE TABLE lessons(id INTEGER PRIMARY KEY, revision_id INTEGER, group_name TEXT, pair_number INTEGER, payload_json TEXT);
                """)
                for i in range(8):
                    day = (start + timedelta(weeks=i)).isoformat()
                    connection.execute("INSERT INTO revisions VALUES(?, ?, 1, 'parsed', ?)", (i + 1, day, json.dumps([group])))
                    connection.execute("INSERT INTO lessons VALUES(?, ?, ?, 1, ?)",
                                       (i + 1, i + 1, group, json.dumps({'subject': 'Физика', 'lesson_status': 'scheduled'})))
                connection.execute("INSERT INTO revisions VALUES(9, ?, 2, 'parsed', ?)", (start.isoformat(), json.dumps([group])))
            target = start + timedelta(weeks=8)
            with sqlite3.connect(path) as connection:
                rows = history_for(connection, group, target)
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows[0].slots, {})
            self.assertEqual(schedule_prediction(path, group, target, target)['status'], 'preliminary')
            self.assertEqual(schedule_prediction(path, '102-Д9', target, target)['status'], 'unavailable')
            self.assertEqual(schedule_prediction(path, group, target, target - timedelta(days=8))['status'], 'unavailable')
            with sqlite3.connect(path) as connection:
                connection.execute("INSERT INTO revisions VALUES(10, ?, 1, 'parsed', ?)", (target.isoformat(), json.dumps([group])))
            self.assertEqual(schedule_prediction(path, group, target, target), {'status': 'published'})
