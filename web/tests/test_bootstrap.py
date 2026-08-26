import unittest
from unittest.mock import Mock, patch
from datetime import datetime

import app as app_module
import schedule_api as schedule_api_module


class FakeDatabase:
    def execute(self, query, params=()):
        return self

    def fetchall(self):
        return []


class BootstrapApiTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_requires_signed_telegram_user(self):
        with patch.object(app_module, 'authenticated_telegram_user', return_value=None):
            response = self.client.get('/api/bootstrap?date=25.08.2026')

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()['success'])

    def test_rejects_invalid_date_without_upstream_calls(self):
        with (
            patch.object(app_module, 'authenticated_telegram_user', return_value={'id': 42}),
            patch.object(app_module, '_fetch_bootstrap_user') as fetch_user,
        ):
            response = self.client.get('/api/bootstrap?date=2026-08-25')

        self.assertEqual(response.status_code, 400)
        fetch_user.assert_not_called()

    def test_returns_complete_initial_payload(self):
        telegram_user = {'id': 42, 'first_name': 'Test', 'username': 'test'}
        upstream_user = {'role': 'Я студент', 'name_or_group': '101-Д9'}
        schedule = {'group': '101-Д9', 'schedule': ['▪️1 пара – Математика – Иванов – 10']}

        with (
            patch.object(app_module, 'authenticated_telegram_user', return_value=telegram_user),
            patch.object(app_module, '_fetch_bootstrap_user', return_value=(200, upstream_user)),
            patch.object(app_module, '_fetch_bootstrap_schedule', return_value=(200, schedule)),
            patch.object(app_module, 'get_db', return_value=FakeDatabase()),
            patch.object(app_module, 'get_or_create_user', return_value=7),
        ):
            response = self.client.get('/api/bootstrap?date=25.08.2026')

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['user']['role'], 'student')
        self.assertEqual(payload['schedule'], schedule)
        self.assertEqual(payload['reaction_date'], '2026-08-25')
        self.assertTrue(payload['adjacent']['previous']['available'])
        self.assertEqual(payload['adjacent']['previous']['date'], '24.08.2026')
        self.assertTrue(payload['adjacent']['next']['available'])
        self.assertEqual(payload['adjacent']['next']['date'], '26.08.2026')
        self.assertEqual(payload['favorites'], [])
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')

    def test_adjacent_dates_skip_sunday(self):
        saturday = datetime(2026, 8, 29)

        previous_date = app_module._adjacent_study_date(saturday, -1)
        next_date = app_module._adjacent_study_date(saturday, 1)

        self.assertEqual(previous_date.strftime('%d.%m.%Y'), '28.08.2026')
        self.assertEqual(next_date.strftime('%d.%m.%Y'), '31.08.2026')

    def test_only_real_lessons_enable_navigation(self):
        empty_schedule = {'schedule': [f'▪️{i} пара – Нет' for i in range(1, 5)]}
        real_schedule = {'schedule': ['▪️1 пара – Математика – Иванов – 10']}

        self.assertFalse(app_module._schedule_has_real_lessons(empty_schedule))
        self.assertFalse(app_module._schedule_has_real_lessons({'schedule': []}))
        self.assertTrue(app_module._schedule_has_real_lessons(real_schedule))

    def test_unavailable_adjacent_schedules_are_not_exposed(self):
        telegram_user = {'id': 42, 'first_name': 'Test'}
        current_schedule = {'group': '101-Д9', 'schedule': ['▪️1 пара – Математика – Иванов – 10']}

        def fetch_schedule(_user_id, schedule_date):
            if schedule_date == '25.08.2026':
                return 200, current_schedule
            return 404, {'detail': 'Расписание не найдено'}

        with (
            patch.object(app_module, 'authenticated_telegram_user', return_value=telegram_user),
            patch.object(app_module, '_fetch_bootstrap_user', return_value=(200, {'role': 'student'})),
            patch.object(app_module, '_fetch_bootstrap_schedule', side_effect=fetch_schedule),
            patch.object(app_module, 'get_db', return_value=FakeDatabase()),
            patch.object(app_module, 'get_or_create_user', return_value=7),
        ):
            response = self.client.get('/api/bootstrap?date=25.08.2026')

        payload = response.get_json()
        self.assertFalse(payload['adjacent']['previous']['available'])
        self.assertIsNone(payload['adjacent']['previous']['schedule'])
        self.assertFalse(payload['adjacent']['next']['available'])
        self.assertIsNone(payload['adjacent']['next']['schedule'])

    def test_upstream_profile_uses_schedule_service_url(self):
        upstream_response = Mock()
        upstream_response.status_code = 200
        upstream_response.json.return_value = {'role': 'student'}
        with patch.object(app_module.requests, 'get', return_value=upstream_response) as request_get:
            status, _ = app_module._fetch_bootstrap_user(42)

        self.assertEqual(status, 200)
        self.assertEqual(request_get.call_args.args[0], 'http://127.0.0.1:8000/user/42')

    def test_service_worker_is_not_http_cached(self):
        response = self.client.get('/service-worker.js')
        script = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Service-Worker-Allowed'], '/')
        self.assertIn('no-cache', response.headers['Cache-Control'])
        self.assertIn("kkepik-shell-20260826-11", script)
        self.assertIn('navigationCacheKey(url)', script)
        self.assertNotIn('cache.match(SHELL_URL)', script)
        response.close()

    def test_index_uses_versioned_non_blocking_assets(self):
        response = self.client.get('/')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('?v=20260826-11', html)
        self.assertIn('<script defer', html)
        self.assertNotIn('loader.gif', html)
        self.assertNotIn('🐍', html)
        self.assertNotIn('🧩', html)
        self.assertNotIn('🎯', html)
        self.assertNotIn('⭐', html)
        self.assertEqual(html.count('class="compact-game-icon"'), 3)
        self.assertIn('id="groupQuickSwitch"', html)
        self.assertIn('id="groupSelectorManage"', html)
        self.assertNotIn('id="progressHint"', html)
        self.assertIn('id="scheduleActions"', html)
        response.close()

        response = self.client.get('/static/js/main.js')
        script = response.get_data(as_text=True)
        self.assertIn('function renderScheduleEmptyState', script)
        self.assertIn("schedule-priority-first-course", script)
        self.assertIn("schedule-priority-upper-course", script)
        self.assertIn('hasRealLessons', script)
        self.assertIn("hideHomeBackButton(event.persisted)", script)
        self.assertIn("'web_app_setup_back_button'", script)
        self.assertIn("swipeAxis = 'vertical'", script)
        self.assertIn("swipeAxis = 'horizontal'", script)
        self.assertIn("touchcancel", script)
        self.assertIn("e.changedTouches[0].clientX", script)
        self.assertNotIn("e.changedTouches[0].screenX", script)
        response.close()

        response = self.client.get('/static/js/schedule.js')
        script = response.get_data(as_text=True)
        self.assertIn("return { primary: targetTime, secondary }", script)
        self.assertNotIn("primary: `до ${targetTime}`", script)
        response.close()

        response = self.client.get('/static/css/main.css')
        css = response.get_data(as_text=True)
        self.assertRegex(css, r'#pairs_block\s*\{[^}]*touch-action:\s*pan-y;')
        response.close()

        response = self.client.get('/static/js/group_selector.js')
        script = response.get_data(as_text=True)
        self.assertIn('function renderEntitySwitcher', script)
        self.assertIn("button.className = 'group-quick-button'", script)
        response.close()

    def test_empty_reactions_render_zero_count(self):
        response = self.client.get('/static/css/main.css')
        css = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('.reaction-count:empty::after', css)
        self.assertIn('content: "0"', css)
        self.assertIn('.reaction-picker-handle', css)
        self.assertIn('@keyframes reactionSheetIn', css)
        response.close()

        response = self.client.get('/static/js/schedule_reactions.js')
        script = response.get_data(as_text=True)
        self.assertIn('function buildOptimisticReactions', script)
        self.assertIn("document.createElement('button')", script)
        self.assertIn('function showReactionPicker(event)', script)
        self.assertIn('function readRecentReactions', script)
        self.assertIn('function rememberReaction', script)
        response.close()

    def test_schedule_search_uses_current_assets_and_compact_labels(self):
        response = self.client.get('/schedule_search')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('schedule_search.css?v=20260826-11', html)
        self.assertIn('schedule_search.js?v=20260826-11', html)
        self.assertIn('<span>Другая</span>', html)
        self.assertIn('<span>Найти</span>', html)
        self.assertIn('class="search-action-dock"', html)
        response.close()

        response = self.client.get('/static/js/schedule_search.js')
        script = response.get_data(as_text=True)
        self.assertIn("fetchJson('/api/schedule/catalog')", script)
        self.assertNotIn("fetchJson('/api/groups')", script)
        self.assertIn("kkepik.schedule-search.state.v1", script)
        self.assertIn('function restoreSearchState', script)
        self.assertIn('function persistSearchState', script)
        response.close()

    def test_schedule_catalog_combines_current_year_entities(self):
        values = {
            'groups': ['101-Д9-1ИНС'],
            'teachers': ['Иванов И.И.'],
            'audiences': ['84'],
        }
        with patch.object(
            schedule_api_module,
            '_fetch_catalog_values',
            side_effect=lambda kind: values[kind],
        ):
            response = self.client.get('/api/schedule/catalog')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), values)
        self.assertIn('max-age=60', response.headers['Cache-Control'])

    def test_empty_catalog_is_success_for_cached_clients(self):
        upstream_response = Mock(status_code=404)
        with patch.object(schedule_api_module.requests, 'get', return_value=upstream_response):
            response = self.client.get('/api/groups')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'groups': []})

    def test_sudoku_uses_svg_icons_and_versioned_assets(self):
        response = self.client.get('/sudoku')
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('testmain.css?v=20260826-11', html)
        self.assertIn('sudoku.css?v=20260826-11', html)
        self.assertIn('sudoku.js?v=20260826-11', html)
        self.assertIn('class="sudoku-title"', html)
        self.assertEqual(html.count('class="difficulty-icon"'), 3)
        for emoji in ('🧩', '🥉', '🥈', '🥇', '🏆', '❤️', '💔'):
            self.assertNotIn(emoji, html)
        response.close()

        response = self.client.get('/static/js/sudoku.js')
        script = response.get_data(as_text=True)
        for emoji in ('🥉', '🥈', '🥇', '🏆', '❤️', '💔', '🎉'):
            self.assertNotIn(emoji, script)
        response.close()


if __name__ == '__main__':
    unittest.main()
