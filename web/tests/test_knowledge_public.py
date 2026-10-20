import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree

from flask import Flask
import requests

import knowledge_public as public
from public_knowledge_contract import build_openapi


GROUP = '103-Д9-3ИНС'
COLLEGE = {'id': 'kkep', 'name': 'Краснодарский колледж электронного приборостроения',
           'city': 'Краснодар', 'aliases': ['ККЭП']}
HASH = 'a' * 64
CATALOG = {'college': COLLEGE, 'timezone': 'Europe/Moscow', 'groups': [GROUP, '101-Д9-3ИНС'],
           'teachers': ['Иванов И.И.'], 'audiences': ['22'], 'dates': ['2026-10-07', '2026-10-08'],
           'group_dates': {GROUP: ['2026-10-07'], '101-Д9-3ИНС': ['2026-10-08']},
           'updated_at': '2026-10-08T10:00:00+03:00'}


def upstream(payload=None, status=200, mime='application/json', data=None):
    raw = json.dumps(payload, ensure_ascii=False).encode() if data is None else data
    response = Mock(status_code=status, headers={'Content-Type': mime, 'Content-Length': str(len(raw))})
    response.iter_content.return_value = [raw]
    return response


def schedule(chosen='2026-10-07', kind='group', entity=GROUP, status='available', subject='Математика'):
    return {'college': COLLEGE, 'timezone': 'Europe/Moscow', 'entity_type': kind, 'entity': entity,
            'range': {'start': chosen, 'end': chosen}, 'days': [{'date': chosen, 'status': status,
            'lessons': [{'pair_number': 1, 'subject': subject, 'group': GROUP, 'teacher': 'Иванов И.И.',
                         'audience': '22', 'subgroup': None, 'start_time': '08:45', 'end_time': '10:05',
                         'association_status': 'source_columns', 'user_id': 42}],
            'source': {'id': HASH, 'revision': 1, 'received_at': '2026-10-06T20:00:00+03:00',
                       'published_at': None, 'url': 'file:///root/secret', 'path': '/root/secret'}}]}


class PublicKnowledgeTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True)
        self.app.register_blueprint(public.knowledge_public_bp)
        self.client = self.app.test_client()
        public.limiter.reset()
        public._catalog_cache.update(payload=None, expires=0.0)

    def test_api_schedule_is_public_structured_and_strips_private_fields(self):
        fake = upstream(schedule())
        with patch.object(public.requests, 'get', return_value=fake) as get:
            response = self.client.get('/api/v2/schedule', query_string={
                'entity_type': 'group', 'entity': GROUP, 'date': '2026-10-07'},
                headers={'Cookie': 'secret', 'Authorization': 'Bearer secret'})
        self.assertEqual(response.status_code, 200, response.data)
        payload = response.get_json()
        lesson = payload['days'][0]['lessons'][0]
        self.assertEqual(lesson['subject'], 'Математика')
        self.assertNotIn('user_id', lesson)
        self.assertNotIn('/root', response.get_data(as_text=True))
        self.assertEqual(payload['days'][0]['source']['url'], public.PUBLIC_ORIGIN + '/api/v2/sources/' + HASH)
        self.assertEqual(get.call_args.args[0], 'http://127.0.0.1:8000/knowledge/schedule')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('headers', get.call_args.kwargs)
        self.assertEqual(get.call_args.kwargs['timeout'], public.UPSTREAM_TIMEOUT)
        self.assertIn('max-age=30', response.headers['Cache-Control'])
        fake.close.assert_called_once()

    def test_relative_day_resolves_in_college_timezone(self):
        with patch.object(public, '_today', return_value=public.date(2026, 10, 8)):
            with patch.object(public.requests, 'get', return_value=upstream(schedule('2026-10-09'))) as get:
                response = self.client.get('/api/v2/schedule?entity_type=group&entity=103-Д9-3ИНС&date=tomorrow')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.kwargs['params']['date'], '2026-10-09')
        self.assertEqual(str(public.COLLEGE_TIMEZONE), 'Europe/Moscow')

    def test_default_date_is_today_never_latest(self):
        with patch.object(public, '_today', return_value=public.date(2026, 10, 8)):
            with patch.object(public.requests, 'get', return_value=upstream(schedule('2026-10-08'))) as get:
                response = self.client.get('/api/v2/schedule?entity_type=group&entity=103-Д9-3ИНС')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_args.kwargs['params']['date'], '2026-10-08')

    def test_invalid_and_repeated_parameters_do_not_reach_upstream(self):
        queries = [
            'entity_type=group&entity=103&date=2026-02-31',
            'entity_type=group&entity=103&date=08.10.2026',
            'entity_type=profile&entity=103', 'entity_type=group&entity=103&user_id=42',
            'entity_type=group&entity=103&entity=102', 'entity_type=group&entity=103&start=2026-10-01',
            'entity_type=group&entity=103&start=2026-10-01&end=2026-11-01',
            'entity_type=group&entity=103&start=2026-10-02&end=2026-10-01',
            'entity_type=group&entity=103&start=2026-10-01&end=2026-10-02&date=today',
        ]
        for query in queries:
            with self.subTest(query=query), patch.object(public.requests, 'get') as get:
                response = self.client.get('/api/v2/schedule?' + query)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(response.get_json()['code'], 'invalid_request')
            self.assertIn('no-store', response.headers['Cache-Control'])
            get.assert_not_called()

    def test_schedule_never_silently_returns_another_day(self):
        with patch.object(public.requests, 'get', return_value=upstream(schedule('2026-10-06'))):
            response = self.client.get('/api/v2/schedule?entity_type=group&entity=103-Д9-3ИНС&date=2026-10-07')
        self.assertEqual(response.status_code, 502)

    def test_missing_is_not_no_lessons(self):
        payload = schedule(status='missing')
        payload['days'][0].update(lessons=[], source=None)
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/schedule?entity_type=group&entity=103-Д9-3ИНС&date=2026-10-07')
        self.assertEqual(response.get_json()['days'][0]['status'], 'missing')
        self.assertEqual(response.get_json()['coverage']['missing'], 1)

    def test_all_network_failures_are_safe_and_uncached(self):
        for error, status in [(requests.Timeout('/root/secret'), 504),
                              (requests.ConnectionError('/root/secret'), 503)]:
            with patch.object(public.requests, 'get', side_effect=error):
                response = self.client.get('/api/v2/catalog')
            self.assertEqual(response.status_code, status)
            self.assertNotIn('/root', response.get_data(as_text=True))
            self.assertIn('no-store', response.headers['Cache-Control'])

    def test_invalid_json_mime_and_oversized_json_are_rejected(self):
        fakes = [upstream(data=b'no json'), upstream(CATALOG, mime='text/html'),
                 upstream([]), upstream({**CATALOG, 'timezone': []})]
        huge = upstream(CATALOG)
        huge.headers['Content-Length'] = str(public.MAX_JSON_BYTES + 1)
        fakes.append(huge)
        for fake in fakes:
            public._catalog_cache.update(payload=None, expires=0.0)
            with patch.object(public.requests, 'get', return_value=fake):
                response = self.client.get('/api/v2/catalog')
            self.assertEqual(response.status_code, 502, response.data)
            fake.close.assert_called_once()

    def test_ssr_contains_schedule_without_login_or_javascript(self):
        with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(schedule())]):
            response = self.client.get('/colleges/kkep/groups/' + GROUP + '/2026-10-07')
        self.assertEqual(response.status_code, 200, response.data)
        html = response.get_data(as_text=True)
        for value in ('Математика', '08:45', '10:05', 'Иванов И.И.', 'Аудитория 22',
                      'Краснодарский колледж электронного приборостроения', 'Europe/Moscow',
                      'не официальный сайт колледжа', 'редакция 1'):
            self.assertIn(value, html)
        self.assertNotIn('telegram-web-app', html)
        self.assertNotIn('file:///root', html)
        self.assertIn('rel="canonical"', html)
        self.assertIn(public.PUBLIC_ORIGIN, html)
        self.assertIn('href="' + public.PUBLIC_ORIGIN + '/api/v2/sources/' + HASH + '"', html)
        self.assertIn('href="' + public._entity_url('group', GROUP, '/archive') + '"', html)
        self.assertIn('href="' + public._entity_url('group', GROUP, '/week/2026-10-05') + '"', html)
        self.assertIn('>Сегодня</a>', html)
        self.assertIn('Оно не подтверждает, что занятие действительно состоялось', html)

    def test_ssr_escapes_source_content_and_ignores_host_header(self):
        with patch.object(public.requests, 'get', side_effect=[
                upstream(CATALOG), upstream(schedule(subject='<script>alert(1)</script>'))]):
            response = self.client.get('/colleges/kkep/groups/' + GROUP + '/2026-10-07',
                                       headers={'Host': 'attacker.example'})
        html = response.get_data(as_text=True)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('attacker.example', html)

    def test_missing_page_is_honest_and_noindex(self):
        payload = schedule(status='missing')
        payload['days'][0].update(lessons=[], source=None)
        with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(payload)]):
            response = self.client.get('/colleges/kkep/groups/' + GROUP + '/2026-10-07')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Это не означает, что пар не было', response.get_data(as_text=True))
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')

    def test_week_page_has_every_day_in_html(self):
        payload = schedule('2026-10-05')
        payload['range']['end'] = '2026-10-11'
        payload['days'] = [dict(payload['days'][0], date=f'2026-10-{day:02}') for day in range(5, 12)]
        with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(payload)]) as get:
            response = self.client.get('/colleges/kkep/groups/' + GROUP + '/week/2026-10-05')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.get_data(as_text=True).count('class="schedule-day"'), 7)
        self.assertEqual(get.call_args.kwargs['params']['end'], '2026-10-11')

    def test_catalog_cache_is_short_and_does_not_consult_user_profile(self):
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)) as get:
            for path in ('/colleges/kkep', '/colleges/kkep/groups', '/api/v2/catalog'):
                self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn('/user/', get.call_args.args[0])

    def test_archive_links_are_server_rendered_with_status(self):
        coverage = {'college': COLLEGE, 'timezone': 'Europe/Moscow', 'days': [
            {'date': '2026-10-07', 'status': 'available'}, {'date': '2026-10-08', 'status': 'entity_not_found'}]}
        with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(coverage)]):
            response = self.client.get('/colleges/kkep/groups/' + GROUP + '/archive')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('Нет в источнике', response.get_data(as_text=True))
        self.assertIn('2026-10-07', response.get_data(as_text=True))
        self.assertNotIn('Сохранённые даты и честное покрытие', response.get_data(as_text=True))
        self.assertIn('Прежние редакции источников сохранены не всегда', response.get_data(as_text=True))

    def test_directory_copy_is_short_and_keeps_archive_coverage(self):
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            college = self.client.get('/colleges/kkep')
            self.assertEqual(college.status_code, 200)
            html = college.get_data(as_text=True)
            for kind in ('groups', 'teachers', 'audiences'):
                self.assertIn('href="/colleges/kkep/' + kind + '"', html)
                response = self.client.get('/colleges/kkep/' + kind)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('Выберите название — откроется расписание и архив', response.get_data(as_text=True))
            for removed in ('Расписание без регистрации', 'Сегодня, завтра и раньше',
                            'Выберите группу, преподавателя или аудиторию.',
                            'Расписания показываем по исходным документам.',
                            'Непрочитанные источники и пропуски отмечаем прямо'):
                self.assertNotIn(removed, html)
            self.assertIn('В архиве 2 сохранённых дат', html)
            self.assertIn('2026-10-07', html)
            self.assertIn('2026-10-08', html)
            self.assertIn('Отсутствие данных не означает отсутствие пар', html)
            self.assertIn('не официальный сайт колледжа', html)

    def test_docs_copy_has_no_slogans_and_keeps_protocol_details(self):
        response = self.client.get('/agents')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for removed in ('Одно расписание — сайт, бот, API и агенты.',
                        'Без регистрации и ключей доступа.',
                        'Открытые данные · только чтение', 'Как отвечать честно',
                        'Что здесь не хранится', 'Домашка остаётся личным учётом каждого.'):
            self.assertNotIn(removed, html)
        for kept in ('href="/api/v2/openapi.json"', 'href="/api/v2/catalog"',
                     'href="/colleges/kkep"', public.PUBLIC_ORIGIN + '/mcp',
                     'Streamable HTTP, только чтение', 'WebMCP', 'history_complete',
                     'source.url', 'Europe/Moscow', 'published_at',
                     'Это не означает, что пар не было', 'API и MCP не возвращают ДЗ'):
            self.assertIn(kept, html)

    def test_resolve_forwards_only_public_parameters(self):
        payload = {'entity_type': 'group', 'query': '103', 'matches': [GROUP], 'ambiguous': False, 'user_id': 42}
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/resolve?entity_type=group&query=103')
        self.assertEqual(response.get_json(), {key: value for key, value in payload.items() if key != 'user_id'})

    def test_changes_preserve_incomplete_history_warning(self):
        payload = {'date': '2026-10-07', 'entity_type': 'group', 'entity': GROUP, 'revisions': [], 'changes': [],
                   'history_complete': False, 'note': 'Прежние редакции могли не сохраниться', 'private': '/root/key'}
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/changes?entity_type=group&entity=103-Д9-3ИНС&date=2026-10-07')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['history_complete'])
        self.assertNotIn('/root', response.get_data(as_text=True))

    def test_archive_file_time_and_invalid_new_revision_warning_are_preserved(self):
        payload = schedule()
        payload['days'][0]['warning'] = 'Новая редакция не разобрана; показана предыдущая корректная редакция'
        source = payload['days'][0]['source']
        source.update(archived_import=True, received_at_semantics='local_file_mtime',
                      parsed_at='2026-10-08T12:00:00+03:00', academic_year='2026-2027',
                      revision_order='archive_import_order')
        with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(payload)]):
            page = self.client.get('/colleges/kkep/groups/' + GROUP + '/2026-10-07')
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn('Новая редакция не разобрана', html)
        self.assertIn('Время локального файла (архивный импорт)', html)
        self.assertIn('не по подтверждённому времени публикации', html)
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/schedule?entity_type=group&entity=103-Д9-3ИНС&date=2026-10-07')
        self.assertEqual(response.get_json()['days'][0]['source']['received_at_semantics'], 'local_file_mtime')
        self.assertEqual(response.get_json()['days'][0]['warning'], payload['days'][0]['warning'])

    def test_failed_revision_status_and_observation_order_are_preserved(self):
        payload = {'date': '2026-10-07', 'entity_type': 'group', 'entity': GROUP, 'revisions': [
            {'revision': 2, 'status': 'failed', 'source': schedule()['days'][0]['source'], 'lessons': []}],
            'changes': [], 'history_complete': False, 'order_basis': 'observation_order_not_publication', 'note': 'Неполный архив'}
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/changes?entity_type=group&entity=103-Д9-3ИНС&date=2026-10-07')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['revisions'][0]['status'], 'failed')
        self.assertEqual(response.get_json()['order_basis'], 'observation_order_not_publication')

    def test_coverage_without_entity_accepts_explicit_nulls_and_honest_summary(self):
        payload = {'college': COLLEGE, 'timezone': 'Europe/Moscow', 'entity_type': None, 'entity': None,
                   'range': {'start': None, 'end': None}, 'days': [], 'available_dates': [], 'missing_dates': [],
                   'summary': {'total_days': 0, 'complete': True}, 'history_complete': False, 'range_is_calendar': False,
                   'note': 'Архив содержит только сохранившиеся источники'}
        with patch.object(public.requests, 'get', return_value=upstream(payload)):
            response = self.client.get('/api/v2/coverage')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.get_json()['entity_type'])
        self.assertEqual(response.get_json()['summary'], {'total_days': 0, 'complete': True})
        self.assertFalse(response.get_json()['history_complete'])

    def test_sitemap_uses_only_proven_group_dates(self):
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.data)
        urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        self.assertIn(public.PUBLIC_ORIGIN + public._entity_url('group', GROUP, '/2026-10-07'), urls)
        self.assertNotIn(public.PUBLIC_ORIGIN + public._entity_url('group', GROUP, '/2026-10-08'), urls)
        self.assertTrue(all(url.startswith(public.PUBLIC_ORIGIN) for url in urls))

    def test_sitemap_without_presence_metadata_does_not_guess_dates(self):
        catalog = {key: value for key, value in CATALOG.items() if key != 'group_dates'}
        with patch.object(public.requests, 'get', return_value=upstream(catalog)):
            response = self.client.get('/sitemap.xml')
        self.assertNotIn('/2026-10-07', response.get_data(as_text=True))

    def test_special_audience_urls_are_reversible_without_encoded_slashes(self):
        for room in ('С/З', 'С/З_1', 'С/З_4', 'Корпус\\А', '~особая'):
            with self.subTest(room=room):
                catalog = {**CATALOG, 'audiences': [room]}
                public._catalog_cache.update(payload=None, expires=0.0)
                path = public._entity_url('audience', room, '/2026-10-07')
                self.assertNotIn('%2F', path.upper())
                self.assertNotIn('%5C', path.upper())
                self.assertIn('/audiences/~', path)
                with patch.object(public.requests, 'get', side_effect=[
                        upstream(catalog), upstream(schedule(kind='audience', entity=room))]) as get:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(get.call_args.kwargs['params']['entity'], room)
                self.assertIn(public.PUBLIC_ORIGIN + path, response.get_data(as_text=True))
                self.assertEqual(public._decode_entity_segment(public._entity_segment(room)), room)
        self.assertEqual(public._entity_url('group', GROUP), '/colleges/kkep/groups/' + public.quote(GROUP, safe=''))

    def test_special_room_archive_sitemap_and_api_keep_original_name(self):
        room = 'С/З_4'
        catalog = {**CATALOG, 'audiences': [room]}
        coverage = {'college': COLLEGE, 'timezone': 'Europe/Moscow',
                    'days': [{'date': '2026-10-07', 'status': 'available'}]}
        archive_path = public._entity_url('audience', room, '/archive')
        with patch.object(public.requests, 'get', side_effect=[upstream(catalog), upstream(coverage)]) as get:
            archive = self.client.get(archive_path)
        self.assertEqual(archive.status_code, 200, archive.data)
        self.assertEqual(get.call_args.kwargs['params']['entity'], room)
        self.assertIn(public._entity_url('audience', room, '/2026-10-07'), archive.get_data(as_text=True))
        sitemap = self.client.get('/sitemap.xml')
        self.assertIn(archive_path, sitemap.get_data(as_text=True))
        self.assertNotIn('%2F', sitemap.get_data(as_text=True).upper())
        with patch.object(public.requests, 'get', return_value=upstream(schedule(kind='audience', entity=room))) as get:
            response = self.client.get('/api/v2/schedule', query_string={
                'entity_type': 'audience', 'entity': room, 'date': '2026-10-07'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(get.call_args.kwargs['params']['entity'], room)

    def test_invalid_or_noncanonical_slug_never_reaches_schedule_upstream(self):
        # The catalog may be consulted first, but no invalid segment reaches
        # a schedule/resolve endpoint and no arbitrary separator is decoded.
        for segment in ('~', '~bad!', '~AAAA', '~' + 'A' * 1200,
                        '~' + public.base64.urlsafe_b64encode(b'ordinary-name').decode().rstrip('=')):
            public._catalog_cache.update(payload=CATALOG, expires=float('inf'))
            with self.subTest(segment=segment), patch.object(public.requests, 'get') as get:
                response = self.client.get('/colleges/kkep/audiences/' + segment + '/2026-10-07')
            self.assertEqual(response.status_code, 400, response.data)
            get.assert_not_called()

    def test_navigation_at_minimum_day_is_safe_bad_request(self):
        public._catalog_cache.update(payload=CATALOG, expires=float('inf'))
        with patch.object(public.requests, 'get') as get:
            response = self.client.get(public._entity_url('group', GROUP, '/0001-01-01'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('no-store', response.headers['Cache-Control'])
        get.assert_not_called()

    def test_navigation_at_maximum_day_is_safe_bad_request(self):
        public._catalog_cache.update(payload=CATALOG, expires=float('inf'))
        with patch.object(public.requests, 'get') as get:
            response = self.client.get(public._entity_url('group', GROUP, '/9999-12-31'))
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_week_beyond_maximum_date_is_safe_bad_request(self):
        public._catalog_cache.update(payload=CATALOG, expires=float('inf'))
        with patch.object(public.requests, 'get') as get:
            response = self.client.get(public._entity_url('group', GROUP, '/week/9999-12-31'))
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_robots_searchbot_rules_are_not_access_control_or_training_policy(self):
        response = self.client.get('/robots.txt')
        body = response.get_data(as_text=True)
        self.assertIn('User-agent: OAI-SearchBot', body)
        self.assertIn('Allow: /colleges/', body)
        self.assertIn('Disallow: /settings', body)
        self.assertNotIn('User-agent: GPTBot', body)
        self.assertIn('Sitemap:', body)

    def test_source_download_has_hash_identity_safe_name_and_no_path_forwarding(self):
        raw = b'%PDF-1.7 fake'
        source_id = hashlib.sha256(raw).hexdigest()
        fake = upstream(mime='application/pdf', data=raw)
        with patch.object(public.requests, 'get', return_value=fake) as get:
            response = self.client.get('/api/v2/sources/' + source_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, raw)
        self.assertIn('immutable', response.headers['Cache-Control'])
        self.assertIn(source_id[:12], response.headers['Content-Disposition'])
        self.assertNotIn('/root', response.headers['Content-Disposition'])
        self.assertEqual(get.call_args.kwargs['params'], {})
        fake.close.assert_called_once()

    def test_source_unknown_query_bad_hash_bad_mime_bad_identity_do_not_leak(self):
        with patch.object(public.requests, 'get') as get:
            self.assertEqual(self.client.get('/api/v2/sources/' + HASH + '?url=https://bad.example').status_code, 400)
            self.assertEqual(self.client.get('/api/v2/sources/not-a-hash').status_code, 404)
            get.assert_not_called()
        for fake in (upstream(mime='text/html', data=b'<html>/root/key</html>'),
                     upstream(mime='application/pdf', data=b'%PDF-wrong-hash')):
            with patch.object(public.requests, 'get', return_value=fake):
                response = self.client.get('/api/v2/sources/' + HASH)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('/root', response.get_data(as_text=True))
            fake.close.assert_called_once()

    def test_api_rate_limits_return_json_and_no_extra_upstream(self):
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            responses = [self.client.get('/api/v2/catalog') for _ in range(11)]
        self.assertEqual(responses[-1].status_code, 429)
        self.assertEqual(responses[-1].get_json()['code'], 'rate_limited')
        self.assertIn('Retry-After', responses[-1].headers)

    def test_docs_contract_and_optional_webmcp_are_local(self):
        contract = build_openapi()
        self.assertEqual(contract['openapi'], '3.1.0')
        self.assertEqual(len(contract['paths']), 6)
        self.assertEqual(self.client.get('/api/v2/openapi.json').status_code, 200)
        self.assertEqual(self.client.get('/agents').status_code, 200)
        self.assertEqual(self.client.get('/llms.txt').status_code, 200)
        root = Path(__file__).resolve().parents[1]
        script = (root / 'static/js/public_schedule.js').read_text()
        self.assertIn("typeof context.registerTool !== 'function'", script)
        self.assertIn("credentials: 'omit'", script)
        self.assertNotIn('localStorage', script)
        self.assertNotIn('initData', script)


if __name__ == '__main__':
    unittest.main()
