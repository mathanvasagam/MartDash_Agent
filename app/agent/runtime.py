from __future__ import annotations

import json
import threading
from typing import Any

from groq import APIConnectionError, Groq, InternalServerError, RateLimitError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.agent.instructions import build_system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolContext, dispatch_tool
from app.config import get_settings
from app.db.session import session_scope
from app.domain.preferences import PreferenceService


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RateLimitError, APIConnectionError, InternalServerError))


class AgentRuntime:
    def __init__(self) -> None:
        self.settings = get_settings()
        # Groq's SDK retries transient errors by default. Disable those retries so the
        # application has one predictable retry policy through Tenacity below.
        self.client = Groq(api_key=self.settings.groq_api_key, max_retries=0)
        self._history: dict[str, list[dict[str, str]]] = {}
        self._history_lock = threading.RLock()

    def clear_chat(self, chat_id: str) -> None:
        with self._history_lock:
            self._history.pop(chat_id, None)

    def _preferences(self, owner_id: str) -> dict[str, str]:
        with session_scope() as session:
            return PreferenceService(session).all(owner_id)

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _complete(self, messages: list[Any]):
        return self.client.chat.completions.create(
            model=self.settings.groq_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=0.1,
        )

    def respond(self, text: str, context: ToolContext) -> str:
        preferences = self._preferences(context.owner_id)
        system = {"role": "system", "content": build_system_prompt(preferences)}
        with self._history_lock:
            prior = list(self._history.get(context.chat_id, []))

        messages: list[Any] = [system, *prior, {"role": "user", "content": text}]
        final_text = ""

        for _ in range(self.settings.agent_max_tool_rounds):
            response = self._complete(messages)
            message = response.choices[0].message
            messages.append(message)
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                final_text = self._content_to_text(getattr(message, "content", ""))
                break

            for tool_call in tool_calls:
                function = tool_call.function
                raw_args = function.arguments or "{}"
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                result = dispatch_tool(function.name, args, context)
                messages.append(
                    {
                        "role": "tool",
                        "name": function.name,
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            final_text = (
                "I could not safely complete that request within the tool-call limit. "
                "Please retry the operation."
            )

        if not final_text:
            final_text = "Done."

        with self._history_lock:
            history = self._history.setdefault(context.chat_id, [])
            history.extend(
                [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": final_text},
                ]
            )
            self._history[context.chat_id] = history[-24:]
        return final_text

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for part in content:
                if isinstance(part, str):
                    chunks.append(part)
                elif isinstance(part, dict) and "text" in part:
                    chunks.append(str(part["text"]))
                elif hasattr(part, "text"):
                    chunks.append(str(part.text))
            return "\n".join(chunks).strip()
        return str(content).strip()
