#!/usr/bin/env python3
"""Inspect the public sitemap; notify IndexNow only with explicit --submit.

No credentials, bot state, or third-party accounts are accessed. A successful
submission means receipt by IndexNow, not that any URL has been indexed.
"""
import argparse
import json
import re
import sys
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET


PUBLIC_ORIGIN = 'https://kkepik.rub1kub.ru'
PUBLIC_HOST = 'kkepik.rub1kub.ru'
KEY_LOCATION = PUBLIC_ORIGIN + '/indexnow-key.txt'
SITEMAP_LOCATION = PUBLIC_ORIGIN + '/sitemap.xml'
INDEXNOW_ENDPOINTS = {
    'global': 'https://api.indexnow.org/indexnow',
    'bing': 'https://www.bing.com/indexnow',
}
INDEXNOW_ENDPOINT = INDEXNOW_ENDPOINTS['global']
TIMEOUT_SECONDS = 15
MAX_SITEMAP_BYTES = 8 * 1024 * 1024
MAX_KEY_BYTES = 256
MAX_RESPONSE_BYTES = 8192
MAX_URLS = 10000
SITEMAP_NS = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
USER_AGENT = 'KKEPIK-IndexNow/1.0'


class SubmissionError(Exception):
    """An error whose message is safe to print without a response body or key."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the submission body/key, even to another HTTPS host.
        return None


def http_request(url, *, payload=None, byte_limit, expected_types=None):
    headers = {'User-Agent': USER_AGENT}
    if payload is not None:
        headers['Content-Type'] = 'application/json; charset=utf-8'
    req = Request(url, data=payload, headers=headers,
                  method='POST' if payload is not None else 'GET')
    opener = build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(req, timeout=TIMEOUT_SECONDS) as response:
            status = response.status
            if payload is None and status != 200:
                raise SubmissionError(f'Public document returned HTTP {status}.')
            if payload is not None and status not in (200, 202):
                raise SubmissionError(f'IndexNow returned unexpected HTTP {status}.')
            if expected_types is not None:
                mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if mime not in expected_types:
                    raise SubmissionError('Public document has an unexpected content type.')
            length = response.headers.get('Content-Length')
            if length and (not length.isdigit() or int(length) > byte_limit):
                raise SubmissionError('Response exceeds the permitted size or has invalid length.')
            content = response.read(byte_limit + 1)
            if len(content) > byte_limit:
                raise SubmissionError('Response exceeds the permitted size.')
            return status, content
    except HTTPError as exc:
        # Do not print server responses or exception text: either could echo key.
        exc.close()
        raise SubmissionError(f'HTTP {exc.code}; no redirects or retries are permitted.') from None
    except (URLError, TimeoutError, OSError):
        raise SubmissionError('Network or TLS request failed; no retry was attempted.') from None


def validate_key(content):
    try:
        key = content.decode('utf-8').strip()
    except UnicodeError:
        raise SubmissionError('IndexNow key file is not valid UTF-8.') from None
    if not re.fullmatch(r'[A-Za-z0-9-]{8,128}', key):
        raise SubmissionError('IndexNow key file has invalid format.')
    return key


def validate_public_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise SubmissionError('Sitemap contains an invalid URL.')
    if not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise SubmissionError('Sitemap URLs must be ASCII URIs with percent-encoded names.')
    if re.search(r'%(?![0-9A-Fa-f]{2})', value):
        raise SubmissionError('Sitemap contains an invalid percent escape.')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.netloc != PUBLIC_HOST
                or parsed.query or parsed.fragment or '?' in value or '#' in value):
            raise SubmissionError('Sitemap contains a noncanonical or nonpublic URL.')
        if not re.fullmatch(r"[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", parsed.path):
            raise SubmissionError('Sitemap contains a noncanonical URI path.')
        raw_segments = parsed.path.split('/')
        segments = [unquote(segment, encoding='utf-8', errors='strict')
                    for segment in raw_segments]
    except (ValueError, UnicodeError):
        raise SubmissionError('Sitemap contains an invalid URL encoding.') from None
    if any(segment in ('.', '..') or '/' in segment or '\\' in segment or '%' in segment
           or any(ord(char) < 32 or ord(char) == 127 for char in segment)
           for segment in segments):
        raise SubmissionError('Sitemap contains an unsafe URL path.')
    if segments in (['', ''], ['', 'agents'], ['', 'colleges', 'kkep']):
        return value
    if (len(segments) < 4 or segments[:3] != ['', 'colleges', 'kkep']
            or segments[3] not in ('groups', 'teachers', 'audiences')):
        raise SubmissionError('Sitemap URL is outside the public schedule routes.')
    if len(segments) == 4:
        return value
    if not segments[4]:
        raise SubmissionError('Sitemap contains an empty entity segment.')
    if len(segments) == 5:
        return value
    if len(segments) == 6 and segments[5] == 'archive':
        return value
    if len(segments) == 6:
        chosen_date = segments[5]
    elif len(segments) == 7 and segments[5] == 'week':
        chosen_date = segments[6]
    else:
        raise SubmissionError('Sitemap contains an unsupported public route.')
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', chosen_date):
            raise ValueError
        date.fromisoformat(chosen_date)
    except ValueError:
        raise SubmissionError('Sitemap contains an invalid schedule date.') from None
    return value


def parse_sitemap(content):
    if len(content) > MAX_SITEMAP_BYTES:
        raise SubmissionError('Sitemap exceeds the permitted size.')
    try:
        xml_text = content.decode('utf-8')
    except UnicodeError:
        raise SubmissionError('Sitemap must be UTF-8 XML.') from None
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', xml_text, re.IGNORECASE):
        raise SubmissionError('Sitemap must not contain a DTD or entity declarations.')
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, ValueError):
        raise SubmissionError('Sitemap XML is invalid.') from None
    if root.tag != SITEMAP_NS + 'urlset':
        raise SubmissionError('Expected a finite URL sitemap, not a sitemap index.')
    urls, seen, duplicates = [], set(), 0
    for entry in root:
        if entry.tag != SITEMAP_NS + 'url':
            raise SubmissionError('Sitemap contains an unexpected entry.')
        locations = entry.findall(SITEMAP_NS + 'loc')
        if len(locations) != 1:
            raise SubmissionError('Each sitemap entry must contain exactly one URL.')
        url = validate_public_url(locations[0].text)
        if url in seen:
            duplicates += 1
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) > MAX_URLS:
            raise SubmissionError('Sitemap exceeds the single-submission limit of 10000 URLs.')
    if not urls:
        raise SubmissionError('Sitemap contains no public URLs.')
    return urls, duplicates


def inspect_inventory():
    _, key_bytes = http_request(KEY_LOCATION, byte_limit=MAX_KEY_BYTES,
                                expected_types={'text/plain'})
    key = validate_key(key_bytes)
    _, sitemap_bytes = http_request(SITEMAP_LOCATION, byte_limit=MAX_SITEMAP_BYTES,
                                    expected_types={'application/xml', 'text/xml'})
    urls, duplicates = parse_sitemap(sitemap_bytes)
    return key, urls, duplicates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true',
                        help='Send one IndexNow POST; without this flag, only inspect.')
    parser.add_argument('--endpoint', choices=tuple(INDEXNOW_ENDPOINTS), default='global',
                        help='Choose a fixed participating endpoint; no automatic fallback.')
    args = parser.parse_args(argv)
    endpoint = INDEXNOW_ENDPOINTS[args.endpoint]
    try:
        key, urls, duplicates = inspect_inventory()
        report = {'mode': 'submit' if args.submit else 'dry-run',
                  'urls': len(urls), 'duplicates_removed': duplicates,
                  'endpoint': endpoint, 'submitted': False,
                  'indexing_guaranteed': False}
        if args.submit:
            body = json.dumps({'host': PUBLIC_HOST, 'key': key,
                               'keyLocation': KEY_LOCATION, 'urlList': urls},
                              ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            status, _ = http_request(endpoint, payload=body,
                                      byte_limit=MAX_RESPONSE_BYTES)
            report.update(submitted=True, http_status=status,
                          result='received' if status == 200 else 'pending_key_validation')
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except SubmissionError as exc:
        print('IndexNow: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
