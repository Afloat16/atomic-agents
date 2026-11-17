"""MediaWiki errors use HTTP 200 and must not become successful empty searches."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest

from tool.wikipedia_search import WikipediaSearchTool, WikipediaSearchToolInputSchema


@pytest.fixture
def tool(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            params = parse_qs(parts.query, keep_blank_values=True)
            query = params.get("srsearch", [None])[0]
            if parts.path.startswith("/api/rest_v1/page/summary/"):
                body = {"title": "Python", "extract": "Python is a programming language."}
            elif params.get("prop") == ["extracts"]:
                body = {"error": {"code": "ratelimited", "info": "Please wait before requesting another extract."}}
            elif query == "":
                body = {"error": {"code": "nosrsearch", "info": "The srsearch parameter must be set."}}
            elif query == "empty":
                body = {"query": {"search": []}}
            else:
                body = {"query": {"search": [{"title": "Python"}]}}
                if query == "warning":
                    body["warnings"] = {"search": {"*": "A nonfatal warning"}}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    instance = WikipediaSearchTool()
    monkeypatch.setattr(instance, "_api_base", lambda language: f"http://127.0.0.1:{server.server_port}")
    try:
        yield instance
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_sync_search_error_is_not_a_successful_empty_result(tool):
    with pytest.raises(RuntimeError, match="nosrsearch.*srsearch parameter"):
        tool.run(WikipediaSearchToolInputSchema(queries=[""]))


@pytest.mark.asyncio
async def test_async_search_error_is_not_a_successful_empty_result(tool):
    with pytest.raises(RuntimeError, match="nosrsearch.*srsearch parameter"):
        await tool.run_async(WikipediaSearchToolInputSchema(queries=[""]))


@pytest.mark.asyncio
async def test_partial_results_preserve_api_error(tool):
    output = await tool.run_async(WikipediaSearchToolInputSchema(queries=["good", "", "empty"]))
    assert [article.query for article in output.results] == ["good"]
    assert len(output.failures) == 1
    assert output.failures[0].query == ""
    assert "nosrsearch" in output.failures[0].error
    assert "srsearch parameter" in output.failures[0].error


@pytest.mark.asyncio
async def test_empty_matches_do_not_hide_another_failed_query(tool):
    with pytest.raises(RuntimeError, match="nosrsearch"):
        await tool.run_async(WikipediaSearchToolInputSchema(queries=["empty", ""]))


@pytest.mark.asyncio
async def test_successful_empty_search_still_returns_empty_results(tool):
    output = await tool.run_async(WikipediaSearchToolInputSchema(queries=["empty"]))
    assert output.results == []
    assert output.failures == []


@pytest.mark.asyncio
async def test_nonfatal_api_warnings_do_not_discard_search_results(tool):
    output = await tool.run_async(WikipediaSearchToolInputSchema(queries=["warning"]))
    assert [article.title for article in output.results] == ["Python"]
    assert output.failures == []


@pytest.mark.asyncio
async def test_extract_api_error_reaches_existing_optional_fetch_handler(tool, caplog):
    output = await tool.run_async(WikipediaSearchToolInputSchema(queries=["good"], full_text=True))
    assert len(output.results) == 1
    assert output.results[0].summary == "Python is a programming language."
    assert output.results[0].full_text is None
    assert "ratelimited" in caplog.text
    assert "Please wait before requesting another extract" in caplog.text


@pytest.mark.asyncio
async def test_direct_extract_reports_error_code_and_message(tool):
    async with aiohttp.ClientSession() as session:
        with pytest.raises(RuntimeError, match="ratelimited.*Please wait"):
            await tool._fetch_full_extract(session, "Python", "en")
