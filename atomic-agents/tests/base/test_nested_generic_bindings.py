"""A component specialization must also bind parameters inside its IO models."""

import json

import httpx
import instructor
import openai
import pytest
from pydantic import ValidationError

from atomic_agents import AgentConfig, AtomicAgent, BaseIOSchema, BaseTool
from atomic_agents.base.base_prompt import BasePrompt
from atomic_agents.base.base_resource import BaseResource


class Envelope[T](BaseIOSchema):
    """A payload whose type is chosen by the component."""

    payload: T


class Operations:
    def run(self, params):
        return self.output_schema(payload=params.payload)

    read = run
    generate = run


@pytest.mark.parametrize("base,operation", [(BaseTool, "run"), (BaseResource, "read"), (BasePrompt, "generate")])
@pytest.mark.parametrize("concrete", [False, True], ids=["instance", "subclass"])
@pytest.mark.parametrize(
    "shape,expected,valid,invalid",
    [
        ("scalar", Envelope[int], 7, "not an integer"),
        ("list", Envelope[list[int]], [7, 8], ["not an integer"]),
        ("model", Envelope[Envelope[int]], {"payload": 7}, {"payload": "not an integer"}),
    ],
)
def test_nested_component_schema_validation(base, operation, concrete, shape, expected, valid, invalid):
    class Wrapped[T](Operations, base[Envelope[T], Envelope[T]]):
        pass

    class Listed[T](Operations, base[Envelope[list[T]], Envelope[list[T]]]):
        pass

    class Nested[T](Operations, base[Envelope[Envelope[T]], Envelope[Envelope[T]]]):
        pass

    template = {"scalar": Wrapped, "list": Listed, "model": Nested}[shape]

    class Concrete(template[int]):
        pass

    component = Concrete() if concrete else template[int]()
    with pytest.raises(ValidationError):
        component.input_schema(payload=invalid)
    with pytest.raises(ValidationError):
        component.output_schema(payload=invalid)

    result = getattr(component, operation)(component.input_schema(payload=valid))
    assert type(result) is component.output_schema
    assert result.model_dump() == expected(payload=valid).model_dump()
    assert component.input_schema.model_json_schema() == expected.model_json_schema()


def test_nested_forwarded_parameters_and_sibling_isolation():
    class Wrapped[T](Operations, BaseTool[Envelope[T], Envelope[T]]):
        pass

    class Listed[T](Wrapped[list[T]]):
        pass

    integer_tool = Listed[int]()
    string_tool = Listed[str]()
    for tool, valid, invalid in [(integer_tool, [3], ["bad"]), (string_tool, ["ok"], [3]), (integer_tool, [4], ["bad"])]:
        with pytest.raises(ValidationError):
            tool.input_schema(payload=invalid)
        assert tool.run(tool.input_schema(payload=valid)).payload == valid
    assert integer_tool.output_schema is Envelope[list[int]]
    assert string_tool.output_schema is Envelope[list[str]]


def test_partial_model_specialization_keeps_fixed_fields():
    class Pair[Left, Right](BaseIOSchema):
        """A fixed label with a parametrized value."""

        left: Left
        right: Right

    class Wrapped[T](Operations, BaseTool[Pair[str, T], Pair[str, T]]):
        pass

    tool = Wrapped[int]()
    with pytest.raises(ValidationError):
        tool.input_schema(left="label", right="bad")
    assert tool.input_schema(left="label", right=7).model_dump() == {"left": "label", "right": 7}
    assert tool.output_schema.model_json_schema() == Pair[str, int].model_json_schema()


def test_unbound_and_concrete_schemas_keep_their_behavior():
    class Unbound[T](Operations, BaseTool[Envelope[T], Envelope[T]]):
        pass

    class Fixed(Operations, BaseTool[Envelope[int], Envelope[int]]):
        pass

    unbound = Unbound()
    assert unbound.input_schema(payload={"arbitrary": True}).payload == {"arbitrary": True}
    fixed = Fixed()
    assert fixed.input_schema is Envelope[int]
    assert fixed.run(fixed.input_schema(payload=7)).payload == 7


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
@pytest.mark.asyncio
async def test_native_agent_request_keeps_nested_schema_types(async_client):
    class WrappedAgent[T](AtomicAgent[Envelope[T], Envelope[T]]):
        pass

    requests = []

    def respond(request):
        body = json.loads(request.content)
        function = body["tools"][0]["function"]
        requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "nested-schema-test",
                "object": "chat.completion",
                "created": 0,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": function["name"], "arguments": '{"payload": 8}'},
                                }
                            ],
                        },
                    }
                ],
            },
        )

    if async_client:
        async with openai.AsyncOpenAI(
            api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        ) as provider:
            agent = WrappedAgent[int](AgentConfig(client=instructor.from_openai(provider), model="fixture-model"))
            result = await agent.run_async(Envelope[int](payload=7))
    else:
        with openai.OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(respond))) as provider:
            agent = WrappedAgent[int](AgentConfig(client=instructor.from_openai(provider), model="fixture-model"))
            result = agent.run(Envelope[int](payload=7))

    assert len(requests) == 1
    payload_schema = requests[0]["tools"][0]["function"]["parameters"]["properties"]["payload"]
    assert payload_schema["type"] == "integer"
    assert type(result).model_fields["payload"].annotation is int
    assert result.payload == 8
    assert agent.history.history[-1].content is result
