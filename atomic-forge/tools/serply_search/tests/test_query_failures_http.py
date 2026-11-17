"""Keep provider failures distinct from empty results using native HTTP requests."""

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from tool.serply_search import SerplySearchTool, SerplySearchToolConfig, SerplySearchToolInputSchema


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            target = urlsplit(self.path)
            query = parse_qs(target.query)["q"][0]
            search_type = target.path.strip("/").split("/")[-1]
            result_key = {"search": "results", "news": "entries", "scholar": "articles"}[search_type]
            content_type = "application/json"
            if query.startswith("error-"):
                status, body = int(query.removeprefix("error-")), {"error": "request rejected"}
            elif query == "invalid-json":
                status, body = 200, None
            elif query == "gateway-html":
                status, body, content_type = 200, None, "text/html"
            elif query == "empty":
                status, body = 200, {result_key: []}
            else:
                status, body = 200, {result_key: [{"title": "Python", "link": "https://www.python.org/"}]}
            data = json.dumps(body).encode() if body is not None else b"<html>service unavailable</html>"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def make_tool(service):
    return SerplySearchTool(SerplySearchToolConfig(api_key="test-serply-credential", base_url=service))


@pytest.mark.asyncio
@pytest.mark.parametrize("search_type", ["search", "news", "scholar"])
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_all_queries_failed_raises(service, search_type, status):
    with pytest.raises(RuntimeError, match=str(status)):
        await make_tool(service).run_async(SerplySearchToolInputSchema(queries=[f"error-{status}"], search_type=search_type))


@pytest.mark.parametrize("search_type", ["search", "news", "scholar"])
def test_sync_entry_point_surfaces_failures(service, search_type):
    with pytest.raises(RuntimeError, match="401"):
        make_tool(service).run(SerplySearchToolInputSchema(queries=["error-401"], search_type=search_type))


@pytest.mark.asyncio
@pytest.mark.parametrize("search_type", ["search", "news", "scholar"])
async def test_partial_results_include_failed_queries(service, search_type):
    output = await make_tool(service).run_async(
        SerplySearchToolInputSchema(queries=["good", "error-429", "empty"], search_type=search_type)
    )
    assert [item.query for item in output.results] == ["good"]
    assert len(output.failures) == 1
    assert output.failures[0].query == "error-429"
    assert "429" in output.failures[0].error


@pytest.mark.asyncio
@pytest.mark.parametrize("queries", [["empty"], []])
async def test_successful_empty_search_remains_a_success(service, queries):
    output = await make_tool(service).run_async(SerplySearchToolInputSchema(queries=queries))
    assert output.results == []
    assert output.failures == []


@pytest.mark.asyncio
async def test_empty_query_does_not_hide_failed_query(service):
    with pytest.raises(RuntimeError, match="401"):
        await make_tool(service).run_async(SerplySearchToolInputSchema(queries=["empty", "error-401"]))


@pytest.mark.asyncio
async def test_invalid_json_is_not_an_empty_search(service):
    with pytest.raises(RuntimeError, match="Serply query"):
        await make_tool(service).run_async(SerplySearchToolInputSchema(queries=["invalid-json"]))


@pytest.mark.asyncio
async def test_failure_traceback_locals_do_not_retain_authenticated_request_headers(service, caplog):
    with pytest.raises(RuntimeError) as error:
        await make_tool(service).run_async(SerplySearchToolInputSchema(queries=["gateway-html"]))

    captured = "".join(traceback.TracebackException.from_exception(error.value, capture_locals=True).format())
    assert "test-serply-credential" not in captured
    assert "test-serply-credential" not in caplog.text
