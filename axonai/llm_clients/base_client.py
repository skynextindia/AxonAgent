from abc import ABC, abstractmethod
from typing import Any, Optional
import warnings
import re


def normalize_content(response):
    """Normalize LLM response content to a plain string.

    Multiple providers (OpenAI Responses API, Google Gemini 3) return content
    as a list of typed blocks, e.g. [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}].
    Downstream agents expect response.content to be a string. This extracts
    and joins the text blocks, discarding reasoning/metadata blocks.

    This also detects and strips inline <think>...</think> blocks (typical of DeepSeek R1
    from third-party providers) from the main content, saving the reasoning in
    additional_kwargs so it doesn't pollute the final output.
    """
    content = response.content
    if isinstance(content, list):
        texts = [
            item.get("text", "") if isinstance(item, dict) and item.get("type") == "text"
            else item if isinstance(item, str) else ""
            for item in content
        ]
        content = "\n".join(t for t in texts if t)
        response.content = content

    if isinstance(response.content, str) and response.content:
        # Detect and extract <think>...</think> block
        think_match = re.search(r"<think>(.*?)</think>", response.content, re.DOTALL | re.IGNORECASE)
        if think_match:
            reasoning = think_match.group(1).strip()
            # Save in additional_kwargs for thinking propagation
            if not hasattr(response, "additional_kwargs") or response.additional_kwargs is None:
                response.additional_kwargs = {}
            if "reasoning_content" not in response.additional_kwargs:
                response.additional_kwargs["reasoning_content"] = reasoning
            # Remove <think>...</think> block from the main content
            response.content = re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL | re.IGNORECASE).strip()

    return response


class BaseLLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self, model: str, base_url: Optional[str] = None, **kwargs):
        self.model = model
        self.base_url = base_url
        self.kwargs = kwargs

    def get_provider_name(self) -> str:
        """Return the provider name used in warning messages."""
        provider = getattr(self, "provider", None)
        if provider:
            return str(provider)
        return self.__class__.__name__.removesuffix("Client").lower()

    def warn_if_unknown_model(self) -> None:
        """Warn when the model is outside the known list for the provider."""
        if self.validate_model():
            return

        warnings.warn(
            (
                f"Model '{self.model}' is not in the known model list for "
                f"provider '{self.get_provider_name()}'. Continuing anyway."
            ),
            RuntimeWarning,
            stacklevel=2,
        )

    @abstractmethod
    def get_llm(self) -> Any:
        """Return the configured LLM instance."""
        pass

    @abstractmethod
    def validate_model(self) -> bool:
        """Validate that the model is supported by this client."""
        pass
