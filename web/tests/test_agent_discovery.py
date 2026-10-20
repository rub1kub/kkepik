"""Public agent discovery regressions; isolated Flask app, no bot import."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

from flask import Flask, request

import knowledge_public as public
from tests.test_knowledge_public import CATALOG, GROUP, schedule, upstream


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.anchors, self.current_anchor = [], None
        self.description, self.canonical, self.machine_links = None, None, {}
        self.in_head, self.body_seen, self.google_verifications = False, False, []
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'head':
            self.in_head = True
        if tag == 'body':
            self.body_seen = True
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
            self.current_anchor = {'href': attrs['href'], 'text': ''}
            self.anchors.append(self.current_anchor)
        if tag == 'meta' and attrs.get('name') == 'description':
            self.description = attrs.get('content')
        if tag == 'meta' and attrs.get('name') == 'google-site-verification':
            self.google_verifications.append((attrs.get('content'), self.in_head, not self.body_seen))
        if tag == 'link' and attrs.get('rel') in ('service-desc', 'alternate'):
            self.machine_links[attrs['rel']] = attrs
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical = attrs.get('href')

    def handle_data(self, data):
        if self.current_anchor is not None:
            self.current_anchor['text'] += data

    def handle_endtag(self, tag):
        if tag == 'a':
            self.current_anchor = None
        if tag == 'head':
            self.in_head = False


class AgentDiscoveryTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
        self.app.config.update(TESTING=True)
        # Execute only the real global response callback. Importing app.py would
        # initialize production databases and unrelated game integrations.
        module = ast.parse((root / 'app.py').read_text())
        callback = next(node for node in module.body
                        if isinstance(node, ast.FunctionDef) and node.name == 'after_request')
        callback.decorator_list = []
        isolated = ast.Module(body=[callback], type_ignores=[])
        namespace = {'request': request}
        exec(compile(ast.fix_missing_locations(isolated), str(root / 'app.py'), 'exec'), namespace)
        self.app.after_request(namespace['after_request'])
        self.app.register_blueprint(public.knowledge_public_bp)
        public.limiter.reset()
        public._catalog_cache.update(payload=None, expires=0.0)

    def test_deep_get_and_head_are_first_request_without_navigation(self):
        for segment in (GROUP, quote(GROUP, safe='')):
            for method in ('GET', 'HEAD'):
                for suffix in ('', '/2026-10-07'):
                    with self.subTest(segment=segment, method=method, suffix=suffix):
                        public._catalog_cache.update(payload=None, expires=0.0)
                        with patch.object(public, '_today', return_value=public.date(2026, 10, 7)), \
                             patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(schedule())]):
                            response = self.app.test_client().open('/colleges/kkep/groups/' + segment + suffix, method=method)
                        self.assertEqual(response.status_code, 200, response.data)
                        self.assertEqual(response.mimetype, 'text/html')
                        self.assertNotIn('Set-Cookie', response.headers)
                        if method == 'HEAD':
                            self.assertEqual(response.data, b'')
                        else:
                            self.assertIn('Математика', response.get_data(as_text=True))
                            self.assertIn('Иванов И.И.', response.get_data(as_text=True))

    def test_all_day_statuses_are_distinct_in_json_and_html(self):
        for status in ('available', 'no_lessons', 'missing', 'invalid_source', 'entity_not_found'):
            payload = schedule(status=status)
            if status != 'available':
                payload['days'][0]['lessons'] = []
            if status == 'missing':
                payload['days'][0]['source'] = None
            with self.subTest(status=status):
                public._catalog_cache.update(payload=None, expires=0.0)
                with patch.object(public.requests, 'get', return_value=upstream(payload)):
                    response = self.app.test_client().get('/api/v2/schedule', query_string={
                        'entity_type': 'group', 'entity': GROUP, 'date': '2026-10-07'})
                self.assertEqual(response.status_code, 200, response.data)
                result = response.get_json()
                self.assertEqual(result['days'][0]['status'], status)
                self.assertEqual(result['coverage'][status], 1)
                self.assertEqual(result['coverage']['complete'], status in ('available', 'no_lessons'))
                with patch.object(public.requests, 'get', side_effect=[upstream(CATALOG), upstream(payload)]):
                    response = self.app.test_client().get('/colleges/kkep/groups/' + quote(GROUP, safe='') + '/2026-10-07')
                self.assertEqual(response.status_code, 200, response.data)
                if status != 'available':
                    self.assertIn('data-status="' + status + '"', response.get_data(as_text=True))
                if status in ('missing', 'invalid_source', 'entity_not_found'):
                    self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')

    def test_agents_has_real_query_example_links_not_only_code(self):
        response = self.app.test_client().get('/agents')
        self.assertEqual(response.status_code, 200)
        links = Links(response.get_data(as_text=True)).links
        for path in ('/api/v2/resolve', '/api/v2/schedule', '/api/v2/coverage', '/api/v2/changes'):
            with self.subTest(path=path):
                self.assertTrue(any(urlsplit(href).path == path and urlsplit(href).query for href in links), path)
        for path in ('/api/v2/catalog', '/api/v2/openapi.json', '/colleges/kkep'):
            self.assertTrue(any(urlsplit(href).path == path for href in links), path)

    def test_api_examples_are_absolute_exact_and_copyable(self):
        response = self.app.test_client().get('/agents')
        self.assertEqual(response.status_code, 200)
        html = Links(response.get_data(as_text=True))
        group = {'entity_type': ['group'], 'entity': [GROUP]}
        examples = [
            ('/api/v2/resolve', {'entity_type': ['group'], 'query': ['103']}, True),
            ('/api/v2/schedule', {**group, 'date': ['tomorrow']}, False),
            ('/api/v2/schedule', {**group, 'date': ['2026-10-09']}, True),
            ('/api/v2/schedule', {**group, 'start': ['2026-10-05'], 'end': ['2026-10-09']}, False),
            ('/api/v2/schedule', {'entity_type': ['teacher'], 'entity': ['Шульга Н.Н.'], 'date': ['2026-10-09']}, False),
            ('/api/v2/coverage', {**group, 'start': ['2026-10-05'], 'end': ['2026-10-09']}, True),
            ('/api/v2/changes', {**group, 'date': ['2026-10-09']}, True),
        ]
        for path, params, copyable in examples:
            with self.subTest(path=path, params=params):
                anchors = [anchor for anchor in html.anchors if urlsplit(anchor['href']).path == path
                           and parse_qs(urlsplit(anchor['href']).query, keep_blank_values=True) == params]
                self.assertTrue(anchors, 'Missing exact query example')
                for anchor in anchors:
                    self.assertTrue(anchor['href'].startswith(public.PUBLIC_ORIGIN + path + '?'), anchor['href'])
                if copyable:
                    self.assertTrue(any(anchor['href'] in anchor['text'] for anchor in anchors),
                                    'No complete runnable URL is visible in anchor text')

    def test_success_removes_retry_after_without_disabling_real_limits(self):
        client = self.app.test_client()
        with patch('limits.storage.memory.time.time', return_value=1_800_000_000.0), \
             patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            responses = [client.get('/api/v2/catalog') for _ in range(11)]
        for response in responses[:10]:
            self.assertEqual(response.status_code, 200, response.data)
            self.assertNotIn('Retry-After', response.headers)
            for header in ('X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset'):
                self.assertIn(header, response.headers)
        response = responses[-1]
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.get_json()['code'], 'rate_limited')
        self.assertGreaterEqual(int(response.headers['Retry-After']), 1)
        self.assertEqual(response.headers['X-RateLimit-Remaining'], '0')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_final_global_cors_keeps_public_v2_read_only(self):
        for method in ('GET', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                public._catalog_cache.update(payload=None, expires=0.0)
                with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
                    response = self.app.test_client().open('/api/v2/catalog', method=method,
                        headers={'Origin': 'https://agent.example', 'User-Agent': 'telegram-test'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')
                self.assertEqual({value.strip() for value in response.headers['Access-Control-Allow-Methods'].split(',')},
                                 {'GET', 'HEAD', 'OPTIONS'})
                self.assertIn('X-RateLimit-Remaining', response.headers['Access-Control-Expose-Headers'])

    def test_unique_metadata_and_machine_links_for_docs_directories_archive(self):
        pages = [self.app.test_client().get('/agents')]
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            pages.extend(self.app.test_client().get('/colleges/kkep/' + kind)
                         for kind in ('groups', 'teachers', 'audiences'))
        coverage = {'college': CATALOG['college'], 'timezone': 'Europe/Moscow', 'days': [
            {'date': '2026-10-07', 'status': 'available'}]}
        with patch.object(public.requests, 'get', return_value=upstream(coverage)):
            pages.append(self.app.test_client().get('/colleges/kkep/groups/' + quote(GROUP, safe='') + '/archive'))
        descriptions = []
        for response in pages:
            self.assertEqual(response.status_code, 200, response.data)
            html = Links(response.get_data(as_text=True))
            self.assertTrue(html.description)
            descriptions.append(html.description)
            self.assertEqual(html.machine_links['service-desc']['href'], public.PUBLIC_ORIGIN + '/api/v2/openapi.json')
            self.assertEqual(html.machine_links['service-desc']['type'], 'application/json')
            self.assertEqual(html.machine_links['alternate']['href'], public.PUBLIC_ORIGIN + '/llms.txt')
            self.assertEqual(html.machine_links['alternate']['type'], 'text/plain')
        self.assertEqual(len(set(descriptions)), len(descriptions))
        self.assertIn(GROUP, descriptions[-1])

    def test_llms_examples_are_real_markdown_links_shared_with_html(self):
        response = self.app.test_client().get('/llms.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/plain')
        urls = set(re.findall(r'\[[^\]\n]+\]\((https://[^)\n]+)\)', response.get_data(as_text=True)))
        examples = public._agent_examples()
        self.assertEqual(len(examples), 7)
        self.assertTrue(set(examples.values()) <= urls, 'Missing linked runnable examples')
        html = Links(self.app.test_client().get('/agents').get_data(as_text=True))
        self.assertTrue(set(examples.values()) <= set(html.links))
        for name, url in examples.items():
            self.assertTrue(url.startswith(public.PUBLIC_ORIGIN + '/api/v2/'), name)
            self.assertTrue(parse_qs(urlsplit(url).query), name)

    def test_sitemap_has_root_and_is_bounded_without_ownership_key(self):
        with patch.object(public.requests, 'get', return_value=upstream(CATALOG)):
            response = self.app.test_client().get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        urls = [element.text for element in ElementTree.fromstring(response.data).iter(
            '{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        self.assertIn(public.PUBLIC_ORIGIN + '/', urls)
        self.assertNotIn(public.PUBLIC_ORIGIN + '/indexnow-key.txt', urls)
        self.assertEqual(len(urls), len(set(urls)))
        self.assertLessEqual(len(urls), 45000)
        self.assertTrue(all(url.startswith(public.PUBLIC_ORIGIN + '/') for url in urls))

    def test_indexnow_key_is_only_one_fixed_public_file_with_get_and_head(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        key_path = Path(fixture.name) / 'indexnow-key.txt'
        key_path.write_bytes(b'publication-test-indexnow-key\n')
        path_patch = patch.object(public, '__file__', str(Path(fixture.name) / 'knowledge_public.py'))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        expected = key_path.read_bytes()
        self.assertRegex(expected.decode('ascii').strip(), r'^[A-Za-z0-9-]{8,128}$')
        client = self.app.test_client()
        with patch.object(public, 'send_file', wraps=public.send_file) as send:
            response = client.get('/indexnow-key.txt')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'text/plain')
            self.assertEqual(response.headers['X-Robots-Tag'], 'noindex')
            self.assertTrue(response.data == expected, 'Ownership file content differs')
            head = client.head('/indexnow-key.txt')
            self.assertEqual(head.status_code, 200)
            self.assertEqual(head.mimetype, 'text/plain')
            self.assertEqual(head.headers['X-Robots-Tag'], 'noindex')
            self.assertEqual(head.data, b'')
            query = client.get('/indexnow-key.txt?path=/etc/passwd')
            self.assertIn(query.status_code, (200, 400))
            if query.status_code == 200:
                self.assertTrue(query.data == expected, 'Query changed the fixed file target')
            self.assertTrue(all(Path(call.args[0]) == key_path for call in send.call_args_list))
            response.close()
            head.close()
            query.close()
        rules = [rule for rule in self.app.url_map.iter_rules() if 'indexnow' in rule.rule]
        self.assertEqual([rule.rule for rule in rules], ['/indexnow-key.txt'])
        self.assertEqual(rules[0].methods, {'GET', 'HEAD', 'OPTIONS'})
        for path in ('/indexnow-key.txt/database.db', '/indexnow-key.txt/%2e%2e/database.db'):
            self.assertEqual(client.get(path).status_code, 404)

    def test_root_template_canonical_without_importing_production_app(self):
        root = Path(__file__).resolve().parents[1]
        html = Links((root / 'templates' / 'index.html').read_text())
        self.assertEqual(html.canonical, public.PUBLIC_ORIGIN + '/')

    def test_google_ownership_tag_occurs_once_in_root_head_before_body(self):
        root = Path(__file__).resolve().parents[1]
        html = Links((root / 'templates' / 'index.html').read_text())
        self.assertEqual(html.google_verifications, [
            ('a3gtxkoD1rsE2XaMDRNpxkUhISqzRl0GT5RsJbNt14Q', True, True)])

    def test_legacy_global_cors_methods_unchanged(self):
        @self.app.get('/api/legacy-cors-qa')
        def legacy():
            return 'legacy'
        response = self.app.test_client().get('/api/legacy-cors-qa')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({value.strip() for value in response.headers['Access-Control-Allow-Methods'].split(',')},
                         {'GET', 'PUT', 'POST', 'DELETE', 'OPTIONS'})


if __name__ == '__main__':
    unittest.main()
