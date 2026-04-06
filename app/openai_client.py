from __future__ import annotations

from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI


class OpenAIClient:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    @staticmethod
    def _extract_output_text(resp: Any) -> Optional[str]:
        text: Optional[str] = getattr(resp, "output_text", None)
        if isinstance(text, str):
            stripped = text.strip()
            return stripped or None
        return None

    async def summarize_messages(
        self,
        system_prompt: str,
        messages_to_summarize: List[Dict[str, Any]],
        model: str = "gpt-5.4-nano",
        temperature: float = 0.2,
        max_summary_chars: int = 1200,
    ) -> str:
        # Собираем контент в один текст, чтобы сократить размер input для Responses API.
        # Формат: User/Assistant блоки.
        parts: List[str] = []
        for msg in messages_to_summarize:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")

        conversation = "\n".join(parts).strip()
        if not conversation:
            return ""

        prompt = (
            "Summarize the following conversation into a short, factual summary "
            "that preserves user preferences, context, and important facts. "
            "Write in the same language as the conversation. "
            "Keep it concise.\n\n"
            f"Conversation:\n{conversation}\n\nSummary:"
        )

        resp = await self._client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                },
                {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
            ],
            temperature=temperature,
        )

        text = self._extract_output_text(resp)
        if text:
            return text[:max_summary_chars]
        return ""

    async def chat_response(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-5.4-nano",
        temperature: float = 0.6,
    ) -> str:
        if not messages:
            return "🤖 Мне нечего добавить"

        payload = [
            {
                "role": msg["role"],
                "content": [
                    {
                        # Для переноса истории в Responses API:
                        # - user/system -> input_text
                        # - assistant -> output_text (иначе 400)
                        "type": "output_text"
                        if msg.get("role") == "assistant"
                        else "input_text",
                        "text": msg.get("content", ""),
                    }
                ],
            }
            for msg in messages
        ]

        resp = await self._client.responses.create(
            model=model,
            input=payload,
            temperature=temperature,
        )

        # SDK обычно отдаёт output_text; на случай несовпадения версии — пробуем несколько вариантов.
        text = self._extract_output_text(resp)
        if text:
            return text

        # Fallback: иногда структура может отличаться.
        for item in getattr(resp, "output", []) or []:
            # Пробуем найти первое текстовое значение.
            maybe = getattr(item, "text", None)
            if isinstance(maybe, str) and maybe.strip():
                return maybe.strip()[:4096]

        return ""
