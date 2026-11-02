import unittest
from unittest.mock import patch

import knowledge_public as public
from public_knowledge_contract import build_openapi
from tests import test_knowledge_public as fixture


class CalendarPublicTests(unittest.TestCase):
    setUp = fixture.PublicKnowledgeTest.setUp

    def payload(self):
        payload = fixture.schedule(chosen='2026-11-04', status='no_lessons')
        payload['days'][0].update(lessons=[], source=None,
            note='4 ноября — выходной. Занятий нет.',
            calendar={'kind': 'holiday', 'note': '4 ноября — выходной. Занятий нет.', 'private': 'discard'})
        return payload

    def test_calendar_reason_is_public_but_extra_fields_are_not(self):
        with patch.object(public.requests, 'get', return_value=fixture.upstream(self.payload())):
            response = self.client.get('/api/v2/schedule', query_string={
                'entity_type': 'group', 'entity': fixture.GROUP, 'date': '2026-11-04'})
        self.assertEqual(response.status_code, 200)
        day = response.get_json()['days'][0]
        self.assertEqual(day['status'], 'no_lessons')
        self.assertIsNone(day['source'])
        self.assertEqual(day['calendar']['kind'], 'holiday')
        self.assertNotIn('private', day['calendar'])
        self.assertIn('calendar', build_openapi()['components']['schemas']['Day']['properties'])

    def test_public_html_shows_holiday_without_fabricated_source(self):
        with patch.object(public.requests, 'get', side_effect=[fixture.upstream(fixture.CATALOG), fixture.upstream(self.payload())]):
            response = self.client.get('/colleges/kkep/groups/' + fixture.GROUP + '/2026-11-04')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('4 ноября — выходной. Занятий нет.', html)
        self.assertNotIn('Математика', html)
        self.assertNotIn('редакция 1', html)
