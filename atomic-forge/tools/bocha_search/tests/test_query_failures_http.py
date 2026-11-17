"""Exercise failed BoCha queries with native aiohttp requests and responses."""

import asyncio
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest

from tool.bocha_search import BoChaSearchTool, BoChaSearchToolConfig, BoChaSearchToolInputSchema


@pytest.fixture
def service(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            query = request["query"]
            if query.startswith("error-"):
                status, body = int(query.removeprefix("error-")), {"message": "request rejected"}
            elif query == "key-error":
                status, body = 401, {"message": "invalid credential test-bocha-credential"}
            elif query == "invalid-json":
                status, body = 200, None
            else:
                hits = [] if query == "empty" else [{"name": "Python", "url": "https://www.python.org/", "snippet": "Docs"}]
                status, body = 200, {"data": {"webPages": {"value": hits}}}
            data = json.dumps(body).encode() if body is not None else b"not JSON"
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    original_post = aiohttp.ClientSession.post

    def local_post(self, url, **kwargs):
        assert url == "https://api.bochaai.com/v1/web-search"
        return original_post(self, f"http://127.0.0.1:{server.server_port}/search", **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "post", local_post)
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def make_tool():
    return BoChaSearchTool(BoChaSearchToolConfig(api_key="test-bocha-credential"))


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_all_queries_failed_raises(service, mode, status):
    tool = make_tool()
    params = BoChaSearchToolInputSchema(queries=[f"error-{status}"])
    with pytest.raises(RuntimeError, match=str(status)):
        tool.run(params) if mode == "sync" else asyncio.run(tool.run_async(params))


@pytest.mark.asyncio
async def test_partial_results_include_query_failures(service):
    output = await make_tool().run_async(BoChaSearchToolInputSchema(queries=["good", "error-429", "empty"]))
    assert [item.query for item in output.results] == ["good"]
    assert len(output.failures) == 1
    assert output.failures[0].query == "error-429"
    assert "429" in output.failures[0].error


@pytest.mark.asyncio
@pytest.mark.parametrize("queries", [["empty"], []])
async def test_successful_empty_search_remains_a_success(service, queries):
    output = await make_tool().run_async(BoChaSearchToolInputSchema(queries=queries))
    assert output.results == []
    assert output.failures == []


@pytest.mark.asyncio
async def test_empty_query_does_not_hide_failed_query(service):
    with pytest.raises(RuntimeError, match="401"):
        await make_tool().run_async(BoChaSearchToolInputSchema(queries=["empty", "error-401"]))


@pytest.mark.asyncio
async def test_invalid_json_is_not_an_empty_search(service):
    with pytest.raises(RuntimeError, match="Query 'invalid-json' failed"):
        await make_tool().run_async(BoChaSearchToolInputSchema(queries=["invalid-json"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_provider_echoed_credentials_are_redacted(service, caplog, partial):
    params = BoChaSearchToolInputSchema(queries=["key-error", "good"] if partial else ["key-error"])
    if partial:
        result = await make_tool().run_async(params)
        details = result.model_dump_json()
    else:
        with pytest.raises(RuntimeError) as error:
            await make_tool().run_async(params)
        details = "".join(traceback.TracebackException.from_exception(error.value, capture_locals=True).format())
    assert "test-bocha-credential" not in details
    assert "test-bocha-credential" not in caplog.text
    assert "[redacted]" in details
