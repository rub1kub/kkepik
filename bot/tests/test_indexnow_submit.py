"""Isolated tests: no production imports, account access, or network requests."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


spec = importlib.util.spec_from_file_location(
    'submit_indexnow', Path(__file__).parents[1] / 'scripts' / 'submit_indexnow.py')
indexnow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexnow)


def sitemap(*urls):
    from xml.sax.saxutils import escape
    entries = ''.join('<url><loc>' + escape(url) + '</loc></url>' for url in urls)
    return ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + entries + '</urlset>').encode('utf-8')


class URLValidationTests(unittest.TestCase):
    def test_allowed_canonical_routes(self):
        paths = ['/', '/agents', '/colleges/kkep', '/colleges/kkep/groups',
                 '/colleges/kkep/teachers', '/colleges/kkep/audiences',
                 '/colleges/kkep/groups/103-%D0%949-3%D0%98%D0%9D%D0%A1',
                 '/colleges/kkep/teachers/%D0%A8%D1%83%D0%BB%D1%8C%D0%B3%D0%B0%20%D0%9D.%D0%9D./archive',
                 '/colleges/kkep/audiences/~0KEv0JdfNA/2026-10-09',
                 '/colleges/kkep/groups/example/week/2026-10-05']
        for path in paths:
            with self.subTest(path=path):
                url = indexnow.PUBLIC_ORIGIN + path
                self.assertEqual(indexnow.validate_public_url(url), url)

    def test_rejects_private_noncanonical_and_unsafe_urls(self):
        urls = ['http://kkepik.rub1kub.ru/agents', 'https://example.com/agents',
                'https://kkepik.rub1kub.ru.evil.example/agents',
                'https://user@kkepik.rub1kub.ru/agents',
                'https://kkepik.rub1kub.ru:443/agents',
                indexnow.PUBLIC_ORIGIN + '/admin/',
                indexnow.PUBLIC_ORIGIN + '/api/v2/sources/abc',
                indexnow.PUBLIC_ORIGIN + '/settings',
                indexnow.PUBLIC_ORIGIN + '/agents?x=1',
                indexnow.PUBLIC_ORIGIN + '/agents?',
                indexnow.PUBLIC_ORIGIN + '/agents#fragment',
                indexnow.PUBLIC_ORIGIN + '/agents#',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/..',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%2e%2e',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/a%2Fb',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/a%5Cb',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%252e%252e',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%ZZ',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%FF',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/%00',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/<script>',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/x/2026-02-30',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/x/20261009',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/x/arbitrary',
                ' ' + indexnow.PUBLIC_ORIGIN + '/agents',
                indexnow.PUBLIC_ORIGIN + '/colleges/kkep/groups/Шульга']
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(indexnow.SubmissionError):
                    indexnow.validate_public_url(url)


class SitemapTests(unittest.TestCase):
    def test_deduplicates_in_first_seen_order(self):
        a = indexnow.PUBLIC_ORIGIN + '/agents'
        b = indexnow.PUBLIC_ORIGIN + '/colleges/kkep'
        self.assertEqual(indexnow.parse_sitemap(sitemap(a, b, a)), ([a, b], 1))

    def test_rejects_invalid_or_nonfinite_xml(self):
        documents = [b'invalid', b'<sitemapindex/>',
                     '<!DOCTYPE urlset><urlset/>'.encode('utf-16'),
                     b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
                     b'<!DOCTYPE urlset [<!ENTITY x "test">]><urlset/>',
                     b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url/></urlset>',
                     b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><unknown/></urlset>']
        for document in documents:
            with self.subTest(document=document):
                with self.assertRaises(indexnow.SubmissionError):
                    indexnow.parse_sitemap(document)

    def test_private_url_aborts_whole_inventory(self):
        with self.assertRaises(indexnow.SubmissionError):
            indexnow.parse_sitemap(sitemap(indexnow.PUBLIC_ORIGIN + '/agents',
                                           indexnow.PUBLIC_ORIGIN + '/admin/'))

    def test_bounded_unique_urls_and_body(self):
        with patch.object(indexnow, 'MAX_URLS', 1):
            with self.assertRaises(indexnow.SubmissionError):
                indexnow.parse_sitemap(sitemap(indexnow.PUBLIC_ORIGIN + '/agents',
                                               indexnow.PUBLIC_ORIGIN + '/colleges/kkep'))
        with patch.object(indexnow, 'MAX_SITEMAP_BYTES', 1):
            with self.assertRaises(indexnow.SubmissionError):
                indexnow.parse_sitemap(b'ab')


class KeyTests(unittest.TestCase):
    def test_valid_key(self):
        self.assertEqual(indexnow.validate_key(b'abcDEF012345-\n'), 'abcDEF012345-')

    def test_bad_keys(self):
        for key in [b'', b'short', b'a' * 129, b'abcde\nfghij', b'<html>invalid</html>', b'\xff']:
            with self.subTest(key=key):
                with self.assertRaises(indexnow.SubmissionError):
                    indexnow.validate_key(key)


class NetworkTests(unittest.TestCase):
    def response(self, content=b'OK', status=200, mime='text/plain', length=None):
        response = Mock()
        response.status = status
        response.headers = {'Content-Type': mime}
        if length is not None:
            response.headers['Content-Length'] = length
        response.read.return_value = content
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_bounded_get_and_post(self):
        opener = Mock()
        opener.open.return_value = self.response()
        with patch.object(indexnow, 'build_opener', return_value=opener):
            self.assertEqual(indexnow.http_request(indexnow.KEY_LOCATION, byte_limit=256,
                                                  expected_types={'text/plain'}), (200, b'OK'))
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, indexnow.KEY_LOCATION)
        self.assertEqual(req.get_method(), 'GET')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], indexnow.TIMEOUT_SECONDS)

    def test_rejects_oversize_content_type_and_status(self):
        responses = [self.response(content=b'1234'),
                     self.response(length='4'), self.response(length='invalid'),
                     self.response(mime='text/html'), self.response(status=201)]
        for response in responses:
            opener = Mock()
            opener.open.return_value = response
            with self.subTest(response=response), patch.object(indexnow, 'build_opener', return_value=opener):
                with self.assertRaises(indexnow.SubmissionError):
                    indexnow.http_request(indexnow.KEY_LOCATION, byte_limit=3,
                                           expected_types={'text/plain'})

    def test_redirects_and_errors_do_not_leak_key_or_retry(self):
        for status in [301, 302, 307, 400, 403, 422, 429, 500]:
            opener = Mock()
            opener.open.side_effect = HTTPError(indexnow.INDEXNOW_ENDPOINT, status,
                                                'DO-NOT-LOG-SECRET', {}, io.BytesIO(b'SECRET-KEY'))
            with patch.object(indexnow, 'build_opener', return_value=opener):
                with self.assertRaises(indexnow.SubmissionError) as caught:
                    indexnow.http_request(indexnow.INDEXNOW_ENDPOINT, payload=b'SECRET-KEY', byte_limit=100)
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertEqual(opener.open.call_count, 1)
        self.assertIsNone(indexnow.NoRedirects().redirect_request(None, None, 307, '', {}, 'https://evil.example'))

    def test_network_error_is_redacted(self):
        opener = Mock()
        opener.open.side_effect = URLError('SECRET-KEY')
        with patch.object(indexnow, 'build_opener', return_value=opener):
            with self.assertRaises(indexnow.SubmissionError) as caught:
                indexnow.http_request(indexnow.KEY_LOCATION, byte_limit=256)
        self.assertNotIn('SECRET', str(caught.exception))

    def test_submit_receipt_statuses_only(self):
        for status in [200, 202]:
            opener = Mock()
            opener.open.return_value = self.response(status=status)
            with patch.object(indexnow, 'build_opener', return_value=opener):
                self.assertEqual(indexnow.http_request(indexnow.INDEXNOW_ENDPOINT,
                    payload=b'{}', byte_limit=10), (status, b'OK'))
            self.assertEqual(opener.open.call_args.args[0].get_method(), 'POST')


class CLITests(unittest.TestCase):
    def inventory(self):
        return 'TEST-KEY-123456', [indexnow.PUBLIC_ORIGIN + '/agents'], 0

    def test_default_is_dry_run_and_does_not_submit(self):
        with patch.object(indexnow, 'inspect_inventory', return_value=self.inventory()), \
                patch.object(indexnow, 'http_request') as http, patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(indexnow.main([]), 0)
        http.assert_not_called()
        report = json.loads(out.getvalue())
        self.assertEqual(report['mode'], 'dry-run')
        self.assertFalse(report['submitted'])
        self.assertNotIn('TEST-KEY', out.getvalue())

    def test_explicit_submit_uses_one_fixed_endpoint_and_never_prints_key(self):
        for status in [200, 202]:
            with patch.object(indexnow, 'inspect_inventory', return_value=self.inventory()), \
                    patch.object(indexnow, 'http_request', return_value=(status, b'SECRET-RESPONSE')) as http, \
                    patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(indexnow.main(['--submit']), 0)
            http.assert_called_once()
            self.assertEqual(http.call_args.args[0], indexnow.INDEXNOW_ENDPOINT)
            body = json.loads(http.call_args.kwargs['payload'])
            self.assertEqual(body['host'], indexnow.PUBLIC_HOST)
            self.assertEqual(body['keyLocation'], indexnow.KEY_LOCATION)
            self.assertEqual(body['urlList'], self.inventory()[1])
            report = json.loads(out.getvalue())
            self.assertTrue(report['submitted'])
            self.assertFalse(report['indexing_guaranteed'])
            self.assertNotIn('TEST-KEY', out.getvalue())
            self.assertNotIn('SECRET-RESPONSE', out.getvalue())

    def test_inventory_failure_prevents_submit(self):
        with patch.object(indexnow, 'inspect_inventory', side_effect=indexnow.SubmissionError('Invalid sitemap.')), \
                patch.object(indexnow, 'http_request') as http, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(indexnow.main(['--submit']), 1)
        http.assert_not_called()

    def test_bing_endpoint_requires_explicit_selection_and_submit(self):
        with patch.object(indexnow, 'inspect_inventory', return_value=self.inventory()), \
                patch.object(indexnow, 'http_request') as http, patch('sys.stdout', new_callable=io.StringIO) as out:
            self.assertEqual(indexnow.main(['--endpoint', 'bing']), 0)
        http.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['endpoint'], 'https://www.bing.com/indexnow')
        with patch.object(indexnow, 'inspect_inventory', return_value=self.inventory()), \
                patch.object(indexnow, 'http_request', return_value=(200, b'')) as http, \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(indexnow.main(['--endpoint', 'bing', '--submit']), 0)
        http.assert_called_once()
        self.assertEqual(http.call_args.args[0], 'https://www.bing.com/indexnow')

    def test_arbitrary_endpoint_is_rejected_before_any_fetch(self):
        with patch.object(indexnow, 'inspect_inventory') as inventory, \
                patch.object(indexnow, 'http_request') as http, patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as caught:
                indexnow.main(['--endpoint', 'https://evil.example/indexnow', '--submit'])
        self.assertEqual(caught.exception.code, 2)
        inventory.assert_not_called()
        http.assert_not_called()

    def test_failed_submission_has_no_success_report_or_fallback(self):
        with patch.object(indexnow, 'inspect_inventory', return_value=self.inventory()), \
                patch.object(indexnow, 'http_request', side_effect=indexnow.SubmissionError('HTTP 403.')) as http, \
                patch('sys.stdout', new_callable=io.StringIO) as out, \
                patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(indexnow.main(['--submit']), 1)
        http.assert_called_once()
        self.assertEqual(out.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
