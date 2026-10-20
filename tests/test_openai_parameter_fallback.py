"""Offline regression tests for OpenAI-compatible parameter fallback."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.ai.client import OpenAIClient
from src.models import AIConfig, AIProvider

TEMPERATURE_ERROR = RuntimeError(
    "Unsupported value: 'temperature' does not support 0.3 with this model. "
    "Only the default (1) value is supported."
)
MAX_TOKENS_ERROR = RuntimeError(
    "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."
)


class OpenAIParameterFallbackTests(unittest.TestCase):
    def make_client(self, model="custom-model", provider=AIProvider.OPENAI):
        config = AIConfig(
            provider=provider,
            model=model,
            api_key_env="UNUSED_TEST_KEY",
            max_tokens=8192,
        )
        with patch("src.ai.client._resolve_api_key", return_value="TEST_PLACEHOLDER"), patch(
            "src.ai.client.AsyncOpenAI"
        ):
            return OpenAIClient(config)

    @staticmethod
    def response():
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None,
        )

    def test_temperature_then_max_tokens_rejections_are_both_recovered(self):
        client = self.make_client()
        create = AsyncMock(side_effect=[TEMPERATURE_ERROR, MAX_TOKENS_ERROR, self.response()])
        client.client.chat.completions.create = create
        self.assertEqual(asyncio.run(client.complete("system", "user")), "ok")
        self.assertEqual(create.await_count, 3)
        final = create.call_args.kwargs
        self.assertNotIn("temperature", final)
        self.assertNotIn("max_tokens", final)
        self.assertEqual(final["max_completion_tokens"], 8192)

    def test_max_tokens_then_temperature_rejections_are_both_recovered(self):
        client = self.make_client()
        create = AsyncMock(side_effect=[MAX_TOKENS_ERROR, TEMPERATURE_ERROR, self.response()])
        client.client.chat.completions.create = create
        self.assertEqual(asyncio.run(client.complete("system", "user")), "ok")
        self.assertEqual(create.await_count, 3)
        self.assertNotIn("temperature", create.call_args.kwargs)
        self.assertNotIn("max_tokens", create.call_args.kwargs)

    def test_followup_calls_remember_both_capabilities(self):
        client = self.make_client()
        create = AsyncMock(
            side_effect=[TEMPERATURE_ERROR, MAX_TOKENS_ERROR, self.response(), self.response()]
        )
        client.client.chat.completions.create = create

        async def complete_twice():
            await client.complete("system", "user")
            return await client.complete("system", "user", temperature=0)

        self.assertEqual(asyncio.run(complete_twice()), "ok")
        self.assertEqual(create.await_count, 4)
        self.assertNotIn("temperature", create.call_args.kwargs)
        self.assertEqual(create.call_args.kwargs["max_completion_tokens"], 8192)

    def test_gpt6_uses_completion_token_limit_and_recovers_temperature(self):
        client = self.make_client(model="gpt-6-luna")
        create = AsyncMock(side_effect=[TEMPERATURE_ERROR, self.response()])
        client.client.chat.completions.create = create
        self.assertEqual(asyncio.run(client.complete("system", "user")), "ok")
        self.assertEqual(create.call_args_list[0].kwargs["max_completion_tokens"], 8192)
        self.assertNotIn("max_tokens", create.call_args_list[0].kwargs)
        self.assertNotIn("temperature", create.call_args.kwargs)

    def test_unrelated_errors_are_not_retried(self):
        client = self.make_client()
        create = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))
        client.client.chat.completions.create = create
        with self.assertRaisesRegex(RuntimeError, "429"):
            asyncio.run(client.complete("system", "user"))
        self.assertEqual(create.await_count, 1)

    def test_error_after_both_fallbacks_is_not_hidden(self):
        client = self.make_client()
        create = AsyncMock(
            side_effect=[TEMPERATURE_ERROR, MAX_TOKENS_ERROR, RuntimeError("503 Unavailable")]
        )
        client.client.chat.completions.create = create
        with self.assertRaisesRegex(RuntimeError, "503"):
            asyncio.run(client.complete("system", "user"))
        self.assertEqual(create.await_count, 3)

    def test_legacy_models_keep_temperature_and_max_tokens(self):
        client = self.make_client(model="gpt-4o")
        create = AsyncMock(return_value=self.response())
        client.client.chat.completions.create = create
        asyncio.run(client.complete("system", "user"))
        self.assertEqual(create.call_args.kwargs["temperature"], 0.3)
        self.assertEqual(create.call_args.kwargs["max_tokens"], 8192)
        self.assertNotIn("max_completion_tokens", create.call_args.kwargs)

    def test_minimax_temperature_clamp_is_preserved(self):
        client = self.make_client(model="MiniMax-M3", provider=AIProvider.MINIMAX)
        create = AsyncMock(return_value=self.response())
        client.client.chat.completions.create = create
        asyncio.run(client.complete("system", "user", temperature=0))
        self.assertGreater(create.call_args.kwargs["temperature"], 0)


if __name__ == "__main__":
    unittest.main()
