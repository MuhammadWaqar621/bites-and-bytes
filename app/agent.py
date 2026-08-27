"""The agentic loop -- the one file worth reading twice.

The whole idea in five lines of pseudo-code::

    messages = [system, ...history, user]
    loop up to MAX_TOOL_ITERATIONS times:
        reply = model(messages, tools)
        if reply has no tool_calls:   -> return reply.content    # done
        append reply to messages                                 # the ASK
        for each tool_call: run the python function,
            append {"role": "tool", "tool_call_id": id, "content": json}  # the ANSWER
    # loop again: the model now sees the tool results and continues

Three rules that this loop lives or dies by:

1. The assistant message that *requested* the tools must be appended to
   ``messages`` before the results. Skip it and the API rejects the next call,
   because a ``tool`` message with no preceding ``tool_calls`` is invalid.
2. Every ``tool`` message must carry the matching ``tool_call_id``. The model
   fired several calls in parallel; the id is the only thing pairing a result
   with its request.
3. ``tool_calls[].function.arguments`` is a **JSON string**, not a dict. It is
   produced by a language model, so it can be malformed -- parse it defensively
   and feed the parse error back as a tool result instead of crashing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.llm_client import ChatBackend, LLMError
from app.schemas import TOOL_SCHEMAS
from app.store import Session, SessionStore
from app.tools import TOOL_REGISTRY

#: How many past turns to replay to the model. Bounds cost and latency; the
#: cart itself lives in the session, so trimming history never loses an order.
HISTORY_LIMIT = 30

SYSTEM_PROMPT = """You are the ordering assistant for "Bites & Bytes", an Indian \
restaurant. You are warm, brief and concrete.

HARD RULES -- these override everything else:
1. Never state a dish name, price, discount, delivery fee, total, ETA or order \
id unless it came back from a tool in this conversation. If you do not have it, \
call the tool.
2. Never do arithmetic yourself. Totals come from calc_total, always.
3. place_order needs a name, phone, full address, pincode and payment method. \
If any is missing, ask the customer for it in plain language and do NOT call \
place_order yet. Never invent these details. But once you have all five, CALL \
place_order -- do not ask the customer to confirm the same details twice. \
"yes", "place it" or "go ahead" after you already hold the details is the \
confirmation; act on it.
4. When a tool returns "ok": false, tell the customer what actually went wrong \
using the tool's "hint", and offer the alternative it suggests. Do not retry \
the same call with the same arguments.
5. You may call several tools in one turn when they are independent.
6. Never decide on a tool's behalf. If the customer asks to cancel, check an \
order or apply a coupon, CALL THE TOOL and report what it says. You may not \
refuse, confirm or predict an outcome you have not actually observed in a tool \
result this turn -- earlier results in this conversation are already stale.

STYLE:
- Reply in 1-4 short sentences unless listing dishes.
- Prices in rupees, written as Rs.320 or ₹320.
- Use a compact markdown list when showing more than two dishes.
- Confirm what you did ("Added 2 x Paneer Tikka"), then ask the natural next \
question.
"""


@dataclass
class ToolInvocation:
    """One tool call, captured so the UI can show what really happened."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    duration_ms: int
    ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
        }


@dataclass
class AgentReply:
    """What one user message produced: the answer plus the audit trail."""

    message: str
    trace: list[ToolInvocation] = field(default_factory=list)
    iterations: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "trace": [call.as_dict() for call in self.trace],
            "iterations": self.iterations,
        }


class OrderingAgent:
    """Runs the request -> tool_calls -> tool_results -> answer cycle."""

    def __init__(self, backend: ChatBackend, store: SessionStore,
                 max_iterations: int = 6) -> None:
        self._backend = backend
        self._store = store
        self._max_iterations = max_iterations

    # -- public API --------------------------------------------------------

    def respond(self, session: Session, user_message: str) -> AgentReply:
        """Handle one customer message end to end.

        Raises :class:`app.llm_client.LLMError` if the model is unreachable;
        every *tool* failure is handled inside the loop instead.
        """
        session.messages.append({"role": "user", "content": user_message})
        working = [{"role": "system", "content": SYSTEM_PROMPT}] + self._history(session)

        reply = AgentReply(message="")

        for iteration in range(1, self._max_iterations + 1):
            reply.iterations = iteration
            assistant = self._backend.chat(working, TOOL_SCHEMAS)
            tool_calls = getattr(assistant, "tool_calls", None)

            # --- Exit path: the model answered in words. -------------------
            if not tool_calls:
                content = assistant.content or "Sorry, I did not catch that."
                session.messages.append({"role": "assistant", "content": content})
                reply.message = content
                return reply

            # --- Rule 1: record the request before recording the results. --
            request_message = {
                "role": "assistant",
                "content": assistant.content,  # usually None alongside tool_calls
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.function.name,
                                     "arguments": call.function.arguments},
                    }
                    for call in tool_calls
                ],
            }
            working.append(request_message)
            session.messages.append(request_message)

            # --- Run them. Parallel calls arrive together in one list. -----
            for call in tool_calls:
                invocation = self._execute(session, call.function.name,
                                           call.function.arguments)
                reply.trace.append(invocation)

                # --- Rule 2: pair the result to its request by id. ---------
                result_message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": invocation.name,
                    "content": json.dumps(invocation.result, ensure_ascii=False),
                }
                working.append(result_message)
                session.messages.append(result_message)

            # Loop back round: the model now sees the results and continues.

        # --- Safety valve: the model kept calling tools and never concluded.
        fallback = (
            "I ran into a loop working that out. Here is where things stand -- "
            "could you tell me the single next thing you want me to do?"
        )
        session.messages.append({"role": "assistant", "content": fallback})
        reply.message = fallback
        return reply

    # -- internals ---------------------------------------------------------

    def _execute(self, session: Session, name: str, raw_arguments: str) -> ToolInvocation:
        """Dispatch one tool call, converting every failure into a tool result.

        Nothing in here is allowed to raise: an exception would end the
        conversation, whereas an ``ok: false`` result lets the model apologise,
        correct itself and carry on -- which is what a good assistant does.
        """
        started = time.perf_counter()

        # Rule 3: the arguments are a model-authored JSON *string*.
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments were not a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolInvocation(
                name=name, arguments={"_raw": raw_arguments},
                result={"ok": False, "error": f"Could not parse arguments: {exc}",
                        "hint": "Re-issue the call with valid JSON arguments."},
                duration_ms=self._ms(started), ok=False,
            )

        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            # Happens if a schema and the registry drift apart, or the model
            # hallucinates a tool that sounds plausible.
            return ToolInvocation(
                name=name, arguments=arguments,
                result={"ok": False, "error": f"No such tool '{name}'.",
                        "hint": "Available tools: " + ", ".join(TOOL_REGISTRY)},
                duration_ms=self._ms(started), ok=False,
            )

        try:
            result = tool(session, self._store, **arguments)
        except TypeError as exc:
            # Wrong / missing / extra argument names -- a schema-vs-signature
            # mismatch, or the model omitting something marked required.
            result = {"ok": False, "error": f"Bad arguments for {name}: {exc}",
                      "hint": "Check the tool's required parameters and try again."}
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            result = {"ok": False, "error": f"{name} failed: {exc}",
                      "hint": "Tell the customer this step failed and offer to retry."}

        return ToolInvocation(
            name=name, arguments=arguments, result=result,
            duration_ms=self._ms(started), ok=bool(result.get("ok")),
        )

    @staticmethod
    def _history(session: Session) -> list[dict[str, Any]]:
        """The last N messages, trimmed so the window never starts mid-handshake.

        Cutting blindly can leave a ``tool`` message whose ``tool_calls``
        request fell off the front of the window -- the API rejects that with a
        400. So we walk forward to the first ``user`` message and start there.
        """
        window = session.messages[-HISTORY_LIMIT:]
        for index, message in enumerate(window):
            if message["role"] == "user":
                return window[index:]
        return window

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)


__all__ = ["OrderingAgent", "AgentReply", "ToolInvocation", "LLMError", "SYSTEM_PROMPT"]
