"""Validate tool requests using SearXNG's real category parser over local HTTP."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.environ["SEARXNG_SOURCE"])
import searx.plugins  # noqa: E402
from searx.engines import categories, engines, load_engines  # noqa: E402
from searx.favicons import proxy  # noqa: E402
from searx.preferences import Preferences  # noqa: E402
from searx.webadapter import parse_generic  # noqa: E402


ROOT = Path(os.environ["ATOMIC_SOURCE"])
PATHS = [
    "atomic-forge/tools/searxng_search/tool/searxng_search.py",
    "atomic-examples/deep-research/deep_research/tools/searxng_search.py",
    "atomic-examples/orchestration-agent/orchestration_agent/tools/searxng_search.py",
    "atomic-examples/web-search-agent/web_search_agent/tools/searxng_search.py",
]


async def main():
    proxy.init(proxy.FaviconProxyConfig())
    load_engines(
        [
            {
                "name": f"offline {category}",
                "engine": "demo_offline",
                "shortcut": str(index),
                "disabled": False,
                "categories": [category],
                "language_support": False,
                "language": "",
            }
            for index, category in enumerate(["general", "news", "social media"])
        ]
    )
    preferences = Preferences(["simple"], list(categories), engines, searx.plugins.STORAGE)
    observed = []

    async def search(request):
        form = dict(request.query)
        selected = parse_generic(preferences, form, preferences.engines.get_disabled())
        observed.append({"wire_category": form.get("categories"), "selected": [item.category for item in selected]})
        return web.json_response(
            {
                "results": [
                    {
                        "url": "https://example.com/" + item.category.replace(" ", "-"),
                        "title": item.name,
                        "content": "Offline engine result",
                        "category": item.category,
                    }
                    for item in selected
                ]
            }
        )

    app = web.Application()
    app.router.add_get("/search", search)
    mismatches = []
    passed = 0
    async with TestServer(app) as server:
        for index, path in enumerate(PATHS):
            spec = importlib.util.spec_from_file_location(f"native_tool_{index}", ROOT / path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tool = module.SearXNGSearchTool(module.SearXNGSearchToolConfig(base_url=str(server.make_url("/")).rstrip("/")))
            for category in ["general", "news", "social_media", None]:
                output = await tool.run_async(module.SearXNGSearchToolInputSchema(queries=["community discussion"], category=category))
                expected_category = "social media" if category == "social_media" else category or "general"
                expected_url = "https://example.com/" + expected_category.replace(" ", "-")
                record = {"tool": path, "input": category, **observed[-1], "returned": [item.url for item in output.results]}
                print(json.dumps(record))
                if record["returned"] == [expected_url] and output.category == category:
                    passed += 1
                else:
                    mismatches.append(record)
    print(f"Native HTTP category validation: {len(mismatches)} mismatches, {passed} passed")
    if mismatches:
        raise SystemExit(1)


asyncio.run(main())
