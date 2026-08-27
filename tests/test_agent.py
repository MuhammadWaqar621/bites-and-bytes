"""Agent-loop tests driven by a scripted fake model.

The loop is the riskiest code in the project and the most expensive to test
against a real endpoint. So instead we hand :class:`OrderingAgent` a backend
that returns pre-baked replies. That lets us assert the three things that
actually break in production:

* the assistant's tool-call request is recorded before the results
* every ``tool`` message carries the right ``tool_call_id``
* a malformed or unknown call is turned into a tool result, not an exception
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.agent import OrderingAgent
from app.schemas import TOOL_SCHEMAS
from app.store import SessionStore


def tool_call(call_id: str, name: str, arguments: dict | str):
    """Build the shape the OpenAI SDK returns for one requested call."""
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=raw),
    )


class ScriptedBackend:
    """Returns queued replies and records every payload it was sent."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools):
        # Deep-ish copy: the agent keeps mutating the same list.
        self.seen.append([dict(m) for m in messages])
        assert tools is TOOL_SCHEMAS
        return self._replies.pop(0)


def answer(text: str):
    return SimpleNamespace(content=text, tool_calls=None)


def requests(*calls):
    return SimpleNamespace(content=None, tool_calls=list(calls))


def build(replies):
    store = SessionStore()
    agent = OrderingAgent(ScriptedBackend(replies), store)
    return agent, store.get("t"), agent._backend  # noqa: SLF001 - test introspection


def test_plain_answer_needs_no_tools():
    agent, session, backend = build([answer("We are open until 11pm.")])
    reply = agent.respond(session, "when do you close?")

    assert reply.message == "We are open until 11pm."
    assert reply.trace == []
    assert reply.iterations == 1
    assert len(backend.seen) == 1


def test_tool_call_then_answer():
    agent, session, backend = build([
        requests(tool_call("c1", "add_to_cart", {"dish_name": "Garlic Naan", "quantity": 2})),
        answer("Added 2 x Garlic Naan."),
    ])
    reply = agent.respond(session, "two garlic naan please")

    assert reply.message == "Added 2 x Garlic Naan."
    assert [call.name for call in reply.trace] == ["add_to_cart"]
    assert reply.trace[0].ok is True
    # The tool really ran against the real session, not a mock.
    assert session.subtotal() == 160.0


def test_second_request_carries_the_request_and_result_in_order():
    agent, session, backend = build([
        requests(tool_call("c1", "view_cart", {})),
        answer("Your cart is empty."),
    ])
    agent.respond(session, "what's in my cart?")

    # backend.seen[1] is what the model saw on the follow-up call.
    roles = [m["role"] for m in backend.seen[1]]
    assert roles == ["system", "user", "assistant", "tool"]

    assistant, tool_message = backend.seen[1][2], backend.seen[1][3]
    assert assistant["tool_calls"][0]["id"] == "c1"
    assert tool_message["tool_call_id"] == "c1"   # the pairing rule


def test_parallel_calls_are_all_executed_and_paired():
    agent, session, backend = build([
        requests(
            tool_call("a", "add_to_cart", {"dish_name": "Paneer Tikka"}),
            tool_call("b", "add_to_cart", {"dish_name": "Masala Chai", "quantity": 2}),
        ),
        answer("Both added."),
    ])
    reply = agent.respond(session, "paneer tikka and two chais")

    assert len(reply.trace) == 2
    tool_messages = [m for m in backend.seen[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["a", "b"]
    assert session.subtotal() == 440.0


def test_malformed_arguments_become_a_tool_result():
    agent, session, backend = build([
        requests(tool_call("c1", "add_to_cart", "{not valid json")),
        answer("Sorry, let me try that again."),
    ])
    reply = agent.respond(session, "add something")

    assert reply.trace[0].ok is False
    assert "parse" in reply.trace[0].result["error"].lower()


def test_unknown_tool_is_reported_not_raised():
    agent, session, backend = build([
        requests(tool_call("c1", "book_a_table", {"seats": 4})),
        answer("We do not take table bookings here."),
    ])
    reply = agent.respond(session, "book me a table")

    assert reply.trace[0].ok is False
    assert "No such tool" in reply.trace[0].result["error"]


def test_failing_tool_keeps_the_conversation_alive():
    agent, session, backend = build([
        requests(tool_call("c1", "apply_coupon", {"code": "NOPE"})),
        answer("That code isn't valid — try CHEF10."),
    ])
    reply = agent.respond(session, "use coupon NOPE")

    assert reply.trace[0].ok is False
    assert reply.message.startswith("That code")


def test_loop_stops_at_max_iterations():
    # A model that never stops asking for tools must not hang the request.
    replies = [requests(tool_call(f"c{i}", "view_cart", {})) for i in range(6)]
    agent, session, backend = build(replies)
    reply = agent.respond(session, "loop forever")

    assert reply.iterations == 6
    assert "loop" in reply.message.lower()


def test_history_window_never_starts_on_a_tool_message():
    agent, session, backend = build([
        requests(tool_call("c1", "view_cart", {})),
        answer("Empty."),
        answer("Still empty."),
    ])
    agent.respond(session, "cart?")
    agent.respond(session, "and now?")

    # The second turn replays history; the first non-system message must be a
    # user turn, or Azure rejects the request with a 400.
    assert backend.seen[2][1]["role"] == "user"
