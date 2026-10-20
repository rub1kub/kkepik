import tempfile
import unittest
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import patch

from fastapi import FastAPI

from knowledge_api import router
from schedules.knowledge_store import KnowledgeStore
from test_knowledge_store import source_frame


class AsgiClient:
    """Tiny dependency-free client; production has no httpx/testclient deps."""
    def __init__(self, app):
        self.app = app

    def request(self, method, path, params=None):
        async def exchange():
            sent = []
            delivered = False
            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                await asyncio.Event().wait()
            async def send(message):
                sent.append(message)
            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                     "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                     "raw_path": path.encode(), "query_string": urlencode(params or {}).encode(),
                     "root_path": "", "headers": [], "server": ("test", 80), "client": ("test", 123)}
            await self.app(scope, receive, send)
            response = next(m for m in sent if m["type"] == "http.response.start")
            body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
            return SimpleNamespace(status_code=response["status"], content=body,
                                   headers={k.decode(): v.decode() for k,v in response["headers"]},
                                   json=lambda: json.loads(body))
        return asyncio.run(exchange())

    def get(self, path, params=None):
        return self.request("GET", path, params)

    def post(self, path, params=None):
        return self.request("POST", path, params)


class KnowledgeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(self.temp.name)
        source = Path(self.temp.name) / "source.pdf"
        source.write_bytes(b"%PDF test")
        self.sha = self.store.ingest(source, "2026-10-09", source_frame())["sha"]
        self.patch = patch("knowledge_api.get_store", return_value=self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        app = FastAPI()
        app.include_router(router)
        self.client = AsgiClient(app)

    def test_catalog_and_schedule_are_public_structured_read_only(self):
        catalog = self.client.get("/knowledge/catalog")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["college"]["id"], "kkep")
        response = self.client.get("/knowledge/schedule", params={"entity_type": "group", "entity": "103-Д9-3ИНС", "date": "2026-10-09"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["days"][0]["lessons"][0]["teacher"], "Борисова Е.В.")
        self.assertEqual(self.client.post("/knowledge/schedule").status_code, 405)

    def test_unknown_dates_are_200_with_missing_status(self):
        response = self.client.get("/knowledge/schedule", params={"entity_type": "group", "entity": "103-Д9-3ИНС", "date": "2027-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["days"][0]["status"], "missing")

    def test_invalid_parameters_return_422(self):
        for params in ({"entity_type": "private_user", "entity": "x", "date": "today"},
                       {"entity_type": "group", "entity": "x", "date": "2026-13-01"},
                       {"entity_type": "group", "entity": "x", "start": "2026-01-01", "end": "2026-12-01"}):
            self.assertEqual(self.client.get("/knowledge/schedule", params=params).status_code, 422)
        self.assertEqual(self.client.get("/knowledge/coverage", params={"entity_type": "group"}).status_code, 422)

    def test_sources_only_registered_hashes_are_served(self):
        response = self.client.get(f"/knowledge/sources/{self.sha}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF test")
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertEqual(self.client.get("/knowledge/sources/database.db").status_code, 404)
        self.assertEqual(self.client.get("/knowledge/sources/" + "0" * 64).status_code, 404)


if __name__ == "__main__":
    unittest.main()
