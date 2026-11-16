"""Measure the entire public tool call with real ALWAYS_OFF SDK spans."""

import json
import statistics
import time

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from pydantic import model_serializer

from atomic_agents import BaseIOSchema, BaseTool, BaseToolConfig
from atomic_agents.observability import AtomicAgentsInstrumentor, TracingConfig

calls = []


class Payload(BaseIOSchema):
    """The same one-megabyte payload is returned unchanged by each call."""

    text: str

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        calls.append(1)
        return handler(self)


class Echo(BaseTool[Payload, Payload]):
    def run(self, params):
        return params


provider = TracerProvider(sampler=ALWAYS_OFF)
instrumentor = AtomicAgentsInstrumentor()
instrumentor.instrument(tracer_provider=provider, config=TracingConfig(openinference_attributes=False))
tool = Echo(BaseToolConfig(title="echo", description="Return the original payload"))
payload = Payload(text="x" * 1_000_000)
measurements = []
try:
    for _ in range(5):
        assert tool.run(payload) is payload
    for _ in range(5):
        calls.clear()
        started = time.perf_counter()
        for _ in range(100):
            assert tool.run(payload) is payload
        measurements.append({"seconds": time.perf_counter() - started, "serializations": len(calls)})
finally:
    instrumentor.uninstrument()
    provider.shutdown()
print(json.dumps({
    "payload_bytes": len(payload.text.encode("utf-8")),
    "calls_per_trial": 100,
    "trials": measurements,
    "median_run_seconds": statistics.median(item["seconds"] for item in measurements),
    "scope": "full instrumented Echo.run; native SDK ALWAYS_OFF; identity-preserved result",
}, indent=2))
