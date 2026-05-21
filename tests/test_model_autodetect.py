import unittest
import pytest
from axonai.llm_clients.model_catalog import resolve_provider_from_model
from axonai.llm_clients.factory import create_llm_client
from axonai.llm_clients.google_client import GoogleClient
from axonai.llm_clients.openai_client import OpenAIClient
from axonai.llm_clients.anthropic_client import AnthropicClient

@pytest.mark.unit
class ModelAutodetectTests(unittest.TestCase):
    def test_resolve_provider_from_model(self):
        # Test exact match in catalog
        self.assertEqual(resolve_provider_from_model("gpt-5.4-mini"), "openai")
        self.assertEqual(resolve_provider_from_model("gemini-2.5-flash"), "google")
        self.assertEqual(resolve_provider_from_model("claude-sonnet-4-6"), "anthropic")
        
        # Test prefix/pattern matching
        self.assertEqual(resolve_provider_from_model("gemini-1.5-flash"), "google")
        self.assertEqual(resolve_provider_from_model("gpt-4o"), "openai")
        self.assertEqual(resolve_provider_from_model("claude-3-5-sonnet"), "anthropic")
        self.assertEqual(resolve_provider_from_model("deepseek-reasoner"), "deepseek")
        self.assertEqual(resolve_provider_from_model("grok-4.20"), "xai")
        self.assertEqual(resolve_provider_from_model("qwen-plus"), "qwen")
        self.assertEqual(resolve_provider_from_model("glm-5"), "glm")
        self.assertEqual(resolve_provider_from_model("MiniMax-M2.7"), "minimax")

    def test_factory_overrides_provider(self):
        # Override default provider (openai) to google
        client = create_llm_client(provider="openai", model="gemini-2.5-flash")
        self.assertIsInstance(client, GoogleClient)
        
        # Override default provider (openai) to anthropic
        client = create_llm_client(provider="openai", model="claude-sonnet-4-6")
        self.assertIsInstance(client, AnthropicClient)

        # Do NOT override wildcard/custom providers like openrouter, ollama, azure
        client = create_llm_client(provider="openrouter", model="gemini-2.5-flash")
        self.assertIsInstance(client, OpenAIClient)  # OpenRouterClient is an OpenAIClient
        self.assertEqual(client.provider, "openrouter")
