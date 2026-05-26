import unittest
import pytest
from axonai.llm_clients.model_catalog import resolve_provider_from_model
from axonai.llm_clients.factory import create_llm_client
from axonai.llm_clients.openai_client import OpenAIClient

@pytest.mark.unit
class ModelAutodetectTests(unittest.TestCase):
    def test_resolve_provider_from_model(self):
        # Test exact match in catalog
        self.assertEqual(resolve_provider_from_model("deepseek-chat"), "deepseek")
        self.assertEqual(resolve_provider_from_model("deepseek-reasoner"), "deepseek")
        
        # Test prefix/pattern matching
        self.assertEqual(resolve_provider_from_model("deepseek-v4-flash"), "deepseek")
        self.assertEqual(resolve_provider_from_model("deepseek-v4-pro"), "deepseek")
        
        # Test unsupported models return None
        self.assertIsNone(resolve_provider_from_model("gemini-2.5-flash"))
        self.assertIsNone(resolve_provider_from_model("gpt-4o"))
        self.assertIsNone(resolve_provider_from_model("claude-3-5-sonnet"))

    def test_factory_requires_deepseek(self):
        # Verify deepseek resolves successfully
        client = create_llm_client(provider="deepseek", model="deepseek-chat")
        self.assertIsInstance(client, OpenAIClient)
        self.assertEqual(client.provider, "deepseek")
        
        # Verify unsupported provider raises ValueError
        with self.assertRaises(ValueError):
            create_llm_client(provider="openai", model="deepseek-chat")

