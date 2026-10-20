import io
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from flask import Flask
import requests
import schedule_api as api
from public_api_contract import build_openapi
from tests.test_telegram_auth import signed_init_data


def upstream(payload=None, status=200, content_type='application/json'):
    response = Mock(status_code=status, headers={'Content-Type': content_type})
    response.json.return_value = payload
    return response


class PublicScheduleApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY='test-only-session-key')
        self.app.register_blueprint(api.schedule_bp)
        self.client = self.app.test_client()
        api.limiter.reset()
        self.schedule = {'group': '103-Д9-3ИНС', 'date': '12.09.2026',
                         'schedule': ['▪️1 пара – Математика – Иванов И.И. – 22']}

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload['code'], code)
        self.assertEqual(payload['error'], payload['detail'])
        self.assertEqual(payload['request_id'], response.headers['X-Request-ID'])
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_post_and_get_have_identical_schedule_and_bounded_upstream_calls(self):
        response = upstream(self.schedule)
        with patch.object(api.requests, 'post', return_value=response) as post:
            body = {'group': ' 103-д9-3инс ', 'date': '12.09.2026'}
            a = self.client.post('/api/schedule/group', json=body)
            b = self.client.get('/api/schedule/group', query_string=body)
        self.assertEqual(a.status_code, 200)
        self.assertEqual(a.get_json(), self.schedule)
        self.assertEqual(b.get_json(), self.schedule)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['json']['group'], '103-Д9-3ИНС')
            self.assertEqual(call.kwargs['timeout'], api.UPSTREAM_TIMEOUT)
            self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(response.close.call_count, 2)

    def test_all_entity_routes_support_both_methods(self):
        for entity, value in [('teacher', 'Иванов И.И.'), ('audience', '22')]:
            payload = {entity: value, 'date': '12.09.2026', 'schedule': []}
            with patch.object(api.requests, 'post', return_value=upstream(payload)):
                for method in ('get', 'post'):
                    args = {'query_string' if method == 'get' else 'json': {entity: value, 'date': '12.09.2026'}}
                    response = getattr(self.client, method)(f'/api/schedule/{entity}', **args)
                    self.assertEqual(response.get_json(), payload)

    def test_invalid_dates_never_reach_upstream(self):
        for value in ('31.02.2026', '2026-09-12', '1.09.2026', '', 12, [], '12/09/2026'):
            with self.subTest(value=value), patch.object(api.requests, 'post') as post:
                response = self.client.post('/api/schedule/group', json={'group': '103', 'date': value})
                self.assert_error(response, 400, 'invalid_request')
                post.assert_not_called()

    def test_invalid_entities_and_unknown_parameters(self):
        for body in ({}, {'group': ''}, {'group': 103}, {'group': 'x' * 201},
                     {'group': '103\n'}, {'group': '103', 'user_id': 42}, [], None):
            with self.subTest(body=body), patch.object(api.requests, 'post') as post:
                response = self.client.post('/api/schedule/group', data=json.dumps(body), content_type='application/json')
                self.assert_error(response, 400, 'invalid_request')
                post.assert_not_called()

    def test_json_syntax_media_type_and_body_limit(self):
        with patch.object(api.requests, 'post') as post:
            self.assert_error(self.client.post('/api/schedule/group', data='{', content_type='application/json'), 400, 'invalid_request')
            self.assert_error(self.client.post('/api/schedule/group', data='group=103'), 415, 'unsupported_media_type')
            self.assert_error(self.client.post('/api/schedule/group', json={'group': 'x' * 20000}), 413, 'payload_too_large')
            post.assert_not_called()

    def test_repeated_query_parameters_are_rejected(self):
        with patch.object(api.requests, 'post') as post:
            self.assert_error(self.client.get('/api/schedule/group?group=103&group=102'), 400, 'invalid_request')
            post.assert_not_called()

    def test_personal_endpoints_reject_unsigned_ids_and_forged_sessions(self):
        with patch.object(api.requests, 'get') as get, patch.object(api.requests, 'post') as post:
            with self.client.session_transaction() as session:
                session['user_id'] = 1084693264
                session['telegram_user'] = {'id': 1084693264}
            for path in ('/api/me', '/api/schedule/me'):
                response = self.client.get(path, query_string={'user_id': 1084693264})
                self.assert_error(response, 401, 'unauthorized')
            get.assert_not_called()
            post.assert_not_called()

    def test_signed_auth_controls_user_id_and_does_not_leak_extra_fields(self):
        init_data = signed_init_data('test-token', 42, int(time.time()))
        payload = {'user_id': 42, 'role': 'Я студент', 'name_or_group': '103-Д9-3ИНС',
                   'is_class_teacher': False, 'class_group': None, 'internal_secret': 'hidden'}
        with patch('telegram_auth.get_bot_token', return_value='test-token'):
            with patch.object(api.requests, 'get', return_value=upstream(payload)) as get:
                response = self.client.get('/api/me', headers={'X-Telegram-Init-Data': init_data})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['role'], 'student')
            self.assertNotIn('internal_secret', response.get_json())
            self.assertTrue(get.call_args.args[0].endswith('/user/42'))
            self.assertIn('no-store', response.headers['Cache-Control'])
            with patch.object(api.requests, 'post', return_value=upstream(self.schedule)) as post:
                response = self.client.post('/api/schedule/me', json={'date': '12.09.2026', 'initData': init_data})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(post.call_args.args[0].endswith('/getUserSchedule/42'))
            self.assertNotIn('initData', post.call_args.kwargs['json'])

    def test_network_errors_have_distinct_safe_statuses(self):
        for exception, status, code in [(requests.Timeout('secret'), 504, 'upstream_timeout'),
                                         (requests.ConnectionError('secret'), 503, 'service_unavailable')]:
            with patch.object(api.requests, 'post', side_effect=exception):
                response = self.client.post('/api/schedule/group', json={'group': '103'})
            self.assert_error(response, status, code)
            self.assertNotIn('secret', response.get_data(as_text=True))

    def test_upstream_error_does_not_leak_file_inventory(self):
        with patch.object(api.requests, 'get', return_value=upstream({'detail': '/root/private-file.pdf'}, 404)):
            response = self.client.get('/api/schedule/download/groups/12.09.2026')
        self.assert_error(response, 404, 'not_found')
        self.assertNotIn('/root', response.get_data(as_text=True))

    def test_bad_upstream_json_and_wrong_schedule_date_are_not_success(self):
        malformed = upstream()
        malformed.json.side_effect = ValueError('bad JSON')
        responses = [malformed, upstream([]), upstream({'date': '12.09.2026', 'schedule': [{}]}),
                     upstream({**self.schedule, 'date': '11.09.2026'})]
        for fake in responses:
            with patch.object(api.requests, 'post', return_value=fake):
                response = self.client.post('/api/schedule/group', json={'group': '103', 'date': '12.09.2026'})
            self.assert_error(response, 502, 'invalid_upstream_response')
            fake.close.assert_called_once()

    def test_real_empty_catalog_is_distinct_from_service_failure(self):
        with patch.object(api.requests, 'get', return_value=upstream({}, 404)):
            response = self.client.get('/api/groups')
        self.assertEqual(response.get_json(), {'groups': []})
        self.assertEqual(response.status_code, 200)
        with patch.object(api.requests, 'get', side_effect=requests.ConnectionError()):
            self.assert_error(self.client.get('/api/groups'), 503, 'service_unavailable')

    def test_partial_catalog_failure_is_not_cached_as_empty_success(self):
        def fetch(kind):
            if kind == 'teachers':
                raise api.ApiError(503)
            return ['value']
        with patch.object(api, '_fetch_catalog_values', side_effect=fetch):
            self.assert_error(self.client.get('/api/schedule/catalog'), 503, 'service_unavailable')

    def test_catalog_cleaning_and_cache_headers(self):
        with patch.object(api.requests, 'get', return_value=upstream({'groups': ['103', ' 102 ', '103', '']})):
            response = self.client.get('/api/groups')
        self.assertEqual(response.get_json(), {'groups': ['102', '103']})
        self.assertIn('max-age=60', response.headers['Cache-Control'])

    def test_download_stream_is_closed_and_not_relabelled(self):
        fake = upstream(content_type='application/pdf')
        fake.raw = io.BytesIO(b'%PDF-1.7 test')
        with patch.object(api.requests, 'get', return_value=fake) as get:
            response = self.client.get('/api/schedule/download/groups/12.09.2026')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'%PDF-1.7 test')
            self.assertIn('.pdf', response.headers['Content-Disposition'])
            self.assertEqual(response.mimetype, 'application/pdf')
            self.assertTrue(get.call_args.kwargs['stream'])
            response.close()
        fake.close.assert_called_once()
        self.assertTrue(fake.raw.closed)

    def test_download_rejects_invalid_type_date_and_upstream_content(self):
        with patch.object(api.requests, 'get') as get:
            self.assert_error(self.client.get('/api/schedule/download/unknown/12.09.2026'), 400, 'invalid_request')
            self.assert_error(self.client.get('/api/schedule/download/groups/31.02.2026'), 400, 'invalid_request')
            get.assert_not_called()
        fake = upstream(content_type='text/html')
        with patch.object(api.requests, 'get', return_value=fake):
            self.assert_error(self.client.get('/api/schedule/download/groups/12.09.2026'), 502, 'invalid_upstream_response')
        fake.close.assert_called_once()

    def test_rate_limit_is_active_with_retry_header_and_no_extra_upstream_call(self):
        with patch.object(api.requests, 'get', return_value=upstream({'groups': []})) as get:
            responses = [self.client.get('/api/groups') for _ in range(11)]
        self.assertEqual([r.status_code for r in responses[:10]], [200] * 10)
        self.assert_error(responses[-1], 429, 'rate_limited')
        self.assertGreaterEqual(int(responses[-1].headers['Retry-After']), 1)
        self.assertEqual(get.call_count, 10)

    def test_get_and_post_share_limit_and_preflight_does_not_use_it(self):
        with patch.object(api.requests, 'post', return_value=upstream(self.schedule)) as post:
            for _ in range(5):
                self.assertEqual(self.client.get('/api/schedule/group?group=103').status_code, 200)
                self.assertEqual(self.client.post('/api/schedule/group', json={'group': '103'}).status_code, 200)
            self.assertEqual(self.client.options('/api/schedule/group').status_code, 200)
            self.assert_error(self.client.get('/api/schedule/group?group=103'), 429, 'rate_limited')
            self.assertEqual(post.call_count, 10)

    def test_invalid_signed_user_payload_is_unauthorized(self):
        with patch('telegram_auth.validate_init_data', return_value={'user': '[]'}):
            self.assert_error(self.client.get('/api/me'), 401, 'unauthorized')

    def test_application_routing_errors_cors_and_local_docs(self):
        import app as app_module
        client = app_module.app.test_client()
        for path, method, status in [('/api/does-not-exist', 'get', 404), ('/api/groups', 'delete', 405)]:
            self.assert_error(getattr(client, method)(path), status,
                              'not_found' if status == 404 else 'method_not_allowed')
        response = client.options('/api/schedule/group', headers={
            'Origin': 'https://example.org', 'Access-Control-Request-Method': 'POST',
        })
        self.assertEqual(response.headers.getlist('Access-Control-Allow-Origin'), ['*'])
        self.assertNotIn('Access-Control-Allow-Credentials', response.headers)
        self.assertIn('X-Telegram-Init-Data', response.headers['Access-Control-Allow-Headers'])
        response = client.get('/api/docs/')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('/api/openapi.json', html)
        self.assertNotIn('cdn.', html)
        self.assertIn('"persistAuthorization": false', html)

    def test_openapi_matches_registered_routes_and_legacy_static_spec(self):
        spec = self.client.get('/api/openapi.json').get_json()
        self.assertEqual(spec, build_openapi())
        static = Path(__file__).resolve().parents[1] / 'static/swagger.json'
        self.assertEqual(spec, json.loads(static.read_text()))
        rules = list(self.app.url_map.iter_rules())
        for path, operations in spec['paths'].items():
            route = path.replace('{schedule_type}', '<schedule_type>').replace('{date}', '<date>')
            matches = [rule for rule in rules if rule.rule == route]
            self.assertEqual(len(matches), 1, path)
            for method in operations:
                self.assertIn(method.upper(), matches[0].methods)
        self.assertEqual(spec['components']['schemas']['Schedule']['properties']['schedule']['items']['type'], 'string')

    def test_public_contract_only_contains_anonymous_read_operations(self):
        spec = self.client.get('/api/openapi.json').get_json()
        expected_paths = {
            '/api/schedule/group', '/api/schedule/teacher', '/api/schedule/audience',
            '/api/groups', '/api/teachers', '/api/audiences', '/api/schedule/catalog',
            '/api/schedule/download/{schedule_type}/{date}',
        }
        self.assertEqual(set(spec['paths']), expected_paths)
        self.assertNotIn('securitySchemes', spec['components'])
        self.assertNotIn('Profile', spec['components']['schemas'])
        self.assertNotIn('security', spec)
        for path, operations in spec['paths'].items():
            with self.subTest(path=path):
                self.assertEqual(set(operations), {'get'})
                operation = operations['get']
                self.assertNotIn('security', operation)
                self.assertNotIn('requestBody', operation)
                self.assertTrue(all(p['in'] in ('query', 'path') for p in operation.get('parameters', [])))
        text = json.dumps(spec, ensure_ascii=False)
        for forbidden in ('X-Telegram-Init-Data', 'TelegramInitData', '/api/me', '/api/schedule/me', 'Мой профиль', 'Моё расписание'):
            self.assertNotIn(forbidden, text)


if __name__ == '__main__':
    unittest.main()
