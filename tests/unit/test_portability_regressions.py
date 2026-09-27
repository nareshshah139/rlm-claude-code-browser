"""Real REPL and async transport regressions; no paid model calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.api_client import AnthropicClient, APIResponse, OpenAIClient, Provider
from src.config import RLMConfig
from src.epistemic.types import VerificationConfig
from src.orchestrator.core import RLMOrchestrator
from src.prompts import build_rlm_system_prompt
from src.recursive_handler import RecursiveREPL
from src.repl_environment import RLMEnvironment
from src.router_integration import ModelRouter
from src.types import CostLimitError, Message, MessageRole, SessionContext, ToolOutput


def test_deferred_helpers_keep_source_tails():
    env = RLMEnvironment(SessionContext())
    text = "x" * 25000 + "TAIL_IS_REQUIRED"
    assert env._recursive_query("find tail", text).context == text
    assert env._llm_batch([("find tail", text)]).operations[0].context == text
    assert env._summarize(text, max_tokens=100).context == text


def test_prompt_accepts_real_session_dataclasses():
    context = SessionContext(
        messages=[Message(MessageRole.USER, "hello")],
        tool_outputs=[ToolOutput("read", "tool text")],
    )
    assert "1 messages" in build_rlm_system_prompt(context, "find")
    serialized = RecursiveREPL(context)._serialize_context(context)
    assert "hello" in serialized and "tool text" in serialized


@pytest.mark.asyncio
async def test_large_child_executes_code_observes_tail_and_resolves_final_var():
    text = "x" * 25000 + "TAIL_IS_REQUIRED"
    router = MagicMock(spec=ModelRouter)
    router.get_model.return_value = "sonnet"
    router.complete = AsyncMock(
        side_effect=[
            SimpleNamespace(
                content="```python\nanswer = context[-16:]\nprint(answer)\n```", tokens_used=12
            ),
            SimpleNamespace(content="FINAL_VAR: answer", tokens_used=9),
        ]
    )
    parent = RecursiveREPL("", router=router)
    result = await parent.recursive_query("Find the tail", text)
    assert result == "TAIL_IS_REQUIRED"
    assert "TAIL_IS_REQUIRED" not in router.complete.call_args_list[0].kwargs["prompt"]
    assert "TAIL_IS_REQUIRED" in router.complete.call_args_list[1].kwargs["prompt"]
    assert parent._results[0].had_repl
    assert parent.total_tokens_used == 21


@pytest.mark.asyncio
async def test_root_and_child_share_client_and_trajectory_contract():
    config = RLMConfig()
    config.activation.mode = "always"
    config.trajectory.export_enabled = False
    responses = [
        '```python\nop = recursive_query("Find tail", files["doc"], spawn_repl=True)\n```',
        "```python\nanswer = context[-16:]\nprint(answer)\n```",
        "FINAL_VAR: answer",
        '```python\nanswer = working_memory["rq_1"]\nprint(answer)\n```',
        "FINAL_VAR: answer",
    ]
    client = MagicMock()
    client.complete = AsyncMock(
        side_effect=[
            APIResponse(
                content=t,
                input_tokens=10,
                output_tokens=10,
                model="sonnet",
                provider=Provider.ANTHROPIC,
            )
            for t in responses
        ]
    )
    orchestrator = RLMOrchestrator(
        config, client, smart_routing=False, verification_config=VerificationConfig(enabled=False)
    )
    events = [
        e
        async for e in orchestrator.run(
            "Find tail", SessionContext(files={"doc": "x" * 25000 + "TAIL_IS_REQUIRED"})
        )
    ]
    assert any(e.content == "TAIL_IS_REQUIRED" for e in events)
    assert client.complete.await_count == 5


@pytest.mark.asyncio
async def test_sibling_calls_share_global_call_budget():
    config = RLMConfig()
    config.cost_controls.max_recursive_calls_per_turn = 1
    router = MagicMock(spec=ModelRouter)
    router.complete = AsyncMock(return_value=SimpleNamespace(content="done", tokens_used=10))
    root = RecursiveREPL("", router=router, config=config)
    child = RecursiveREPL("", router=router, config=config, parent=root, depth=1)
    root.child_repls.append(child)
    await root.recursive_query("first", "small")
    with pytest.raises(CostLimitError):
        await child.recursive_query("second", "small")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
async def test_provider_transport_is_really_async(provider):
    active = peak = 0

    transport_lib = httpx
    if provider == "anthropic":
        import anthropic._base_client

        transport_lib = getattr(anthropic._base_client, "httpx2", httpx)

    async def respond(_request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if provider == "anthropic":
            body = {
                "id": "test",
                "type": "message",
                "role": "assistant",
                "model": "test",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }
        else:
            body = {
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }
        return transport_lib.Response(200, json=body)

    client = (AnthropicClient if provider == "anthropic" else OpenAIClient)(
        api_key="test-not-a-secret", cost_tracker=MagicMock()
    )
    await client.client.close()
    client.client = type(client.client)(
        api_key="test-not-a-secret",
        http_client=transport_lib.AsyncClient(transport=transport_lib.MockTransport(respond)),
    )
    try:
        results = await asyncio.gather(
            *[client.complete([{"role": "user", "content": "test"}]) for _ in range(3)]
        )
        assert [r.content for r in results] == ["ok"] * 3
        assert peak == 3
    finally:
        await client.client.close()
