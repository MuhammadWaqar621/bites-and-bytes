"""Thin wrapper around the Azure OpenAI chat-completions endpoint.

Why a wrapper at all? Two reasons:

1. The agent loop in :mod:`app.agent` should depend on one small method
   (`chat`), not on the whole vendor SDK. That makes the loop unit-testable
   with a stub client (see ``tests/test_agent.py``).
2. Azure raises a family of different exception types. Translating them into a
   single :class:`LLMError` means the web layer has exactly one thing to catch.
"""

from __future__ import annotations

from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    AzureOpenAI,
    RateLimitError,
)

from app.config import Settings


class LLMError(RuntimeError):
    """Any failure while talking to the model, already phrased for a human."""


class ChatBackend(Protocol):
    """The only capability :class:`app.agent.OrderingAgent` needs from a model.

    Declaring it as a Protocol lets the tests pass in a scripted fake instead of
    calling Azure, with no inheritance and no mocking library.
    """

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """Send the conversation plus the tool catalogue, return the reply message."""


class AzureChatBackend:
    """Real backend: one Azure OpenAI chat-completions call."""

    def __init__(self, settings: Settings) -> None:
        if not settings.is_configured:
            raise LLMError(
                "Azure OpenAI is not configured. Missing: "
                + ", ".join(settings.missing)
                + ". Fill them into the .env file and restart."
            )
        self._settings = settings
        # `azure_endpoint` + `api_version` are what make this Azure rather than
        # api.openai.com; the SDK builds the /openai/deployments/... URL for us.
        self._client = AzureOpenAI(
            azure_endpoint=settings.endpoint,
            api_key=settings.api_key,
            api_version=settings.api_version,
        )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Any:
        """One round-trip to the model.

        Returns the assistant message. It carries EITHER ``content`` (a normal
        answer) OR ``tool_calls`` (a request for us to run functions) — the
        agent loop branches on which one is present.
        """
        try:
            response = self._client.chat.completions.create(
                # On Azure, `model` is the DEPLOYMENT name, not the model name.
                model=self._settings.deployment,
                messages=messages,
                tools=tools,
                # "auto" = the model decides whether to answer or call a tool.
                tool_choice="auto",
                temperature=self._settings.temperature,
            )
        except AuthenticationError as exc:
            raise LLMError(
                "Azure rejected the API key. Check LLM_ENDPOINT_MINI_MODEL_APIKEY."
            ) from exc
        except RateLimitError as exc:
            raise LLMError(
                "Azure rate limit hit (429). Wait a moment and send the message again."
            ) from exc
        except APIConnectionError as exc:
            raise LLMError(
                "Could not reach the Azure endpoint. Check LLM_ENDPOINT_MINI_MODEL "
                "and your network connection."
            ) from exc
        except APIStatusError as exc:
            # The most common one here is 404: the deployment name is wrong.
            raise LLMError(
                f"Azure returned HTTP {exc.status_code}. If it is 404, the deployment "
                f"'{self._settings.deployment}' does not exist on this resource."
            ) from exc

        return response.choices[0].message
