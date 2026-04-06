from __future__ import annotations

import io
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

    async def chat_response_with_image(
        self,
        messages: List[Dict[str, Any]],
        image_data_url: str,
        model: str = "gpt-5.4-nano",
        temperature: float = 0.6,
    ) -> str:
        """
        Generate a response conditioned on the provided image.

        The image is attached to the last `user` message in `messages` by replacing
        that message's content with both:
        - input_text (the message text)
        - input_image (the image_data_url)
        """
        if not messages:
            return "🤖 Мне нечего добавить"

        last_user_idx: int | None = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                last_user_idx = i

        payload: List[Dict[str, Any]] = []
        for i, msg in enumerate(messages):
            role = msg.get("role")
            content_text = msg.get("content", "") or ""

            if role == "assistant":
                payload.append(
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": content_text},
                        ],
                    }
                )
                continue

            # system/user -> input_text, except last user message where we also add input_image.
            if role in {"system", "user"} and i == last_user_idx:
                parts: List[Dict[str, Any]] = []
                if content_text.strip():
                    parts.append({"type": "input_text", "text": content_text})
                # For Responses API vision input, `image_url` must be a string
                # (fully-qualified URL or base64 data URL).
                parts.append({"type": "input_image", "image_url": image_data_url})
                payload.append({"role": role, "content": parts})
            else:
                payload.append(
                    {
                        "role": role,
                        "content": [
                            {"type": "input_text", "text": content_text},
                        ],
                    }
                )

        resp = await self._client.responses.create(
            model=model,
            input=payload,
            temperature=temperature,
        )

        text = self._extract_output_text(resp)
        if text:
            return text

        # Fallback: sometimes structure can differ.
        for item in getattr(resp, "output", []) or []:
            maybe = getattr(item, "text", None)
            if isinstance(maybe, str) and maybe.strip():
                return maybe.strip()[:4096]

        return ""

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.ogg",
        model: str = "gpt-4o-mini-transcribe",
        language: str | None = None,
    ) -> str:
        """
        Speech-to-text using the Audio Transcriptions endpoint.

        Returns plain text transcription.
        """
        if not audio_bytes:
            return ""

        audio_file = io.BytesIO(audio_bytes)
        # The OpenAI SDK uses `name` to infer extension/content handling.
        audio_file.name = filename

        # Docs examples return an object with `.text` when response_format="text".
        kwargs: Dict[str, Any] = {
            "model": model,
            "file": audio_file,
            "response_format": "text",
        }
        if language:
            # Optional hint for transcription quality.
            kwargs["language"] = language

        resp = await self._client.audio.transcriptions.create(**kwargs)
        text = getattr(resp, "text", None)
        if isinstance(text, str):
            return text.strip()
        # Fallback if SDK structure differs.
        maybe = str(resp).strip()
        return maybe
