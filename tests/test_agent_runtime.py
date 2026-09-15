import threading
from types import SimpleNamespace

from app.agent.instructions import build_system_prompt
from app.agent.runtime import AgentRuntime
from app.agent.tools import ToolContext


def test_agent_observe_act_observe_loop(monkeypatch):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.settings = SimpleNamespace(agent_max_tool_rounds=4)
    runtime._history = {}
    runtime._history_lock = threading.RLock()

    monkeypatch.setattr(runtime, "_preferences", lambda owner_id: {"default_payment": "UPI"})

    first_message = SimpleNamespace(
        content="",
        tool_calls=[
            SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="get_stock", arguments='{"product":"MAGGI-70G"}'),
            )
        ],
    )
    second_message = SimpleNamespace(content="10 packets are available.", tool_calls=[])
    responses = iter(
        [
            SimpleNamespace(choices=[SimpleNamespace(message=first_message)]),
            SimpleNamespace(choices=[SimpleNamespace(message=second_message)]),
        ]
    )
    monkeypatch.setattr(runtime, "_complete", lambda messages: next(responses))

    calls = []

    def fake_dispatch(name, args, context):
        calls.append((name, args, context.owner_id))
        return {"ok": True, "product": {"name": "Maggi 70g", "stock_quantity": "10"}}

    monkeypatch.setattr("app.agent.runtime.dispatch_tool", fake_dispatch)

    result = runtime.respond(
        "how much Maggi is left?",
        ToolContext(owner_id="owner", chat_id="chat", update_id=1),
    )

    assert result == "10 packets are available."
    assert calls == [("get_stock", {"product": "MAGGI-70G"}, "owner")]
    assert runtime._history["chat"][-1]["content"] == result


def test_clear_chat_only_clears_conversation_state():
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime._history = {"chat": [{"role": "user", "content": "hello"}]}
    runtime._history_lock = threading.RLock()
    runtime.clear_chat("chat")
    assert "chat" not in runtime._history


def test_agent_prompt_requires_accessible_product_formatting():
    prompt = build_system_prompt({})

    assert "Never use Markdown tables" in prompt
    assert "screen readers" in prompt
    assert "Keep each line short" in prompt
