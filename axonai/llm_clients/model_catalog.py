"""Shared model catalog for CLI selections and validation."""

from __future__ import annotations

from typing import Dict, List, Tuple

ModelOption = Tuple[str, str]
ProviderModeOptions = Dict[str, Dict[str, List[ModelOption]]]


MODEL_OPTIONS: ProviderModeOptions = {
    "deepseek": {
        "quick": [
            ("DeepSeek V4 Flash - Latest V4 fast model", "deepseek-v4-flash"),
            ("DeepSeek V3.2", "deepseek-chat"),
            ("Custom model ID", "custom"),
        ],
        "deep": [
            ("DeepSeek V4 Pro - Latest V4 flagship model", "deepseek-v4-pro"),
            ("DeepSeek V3.2 (thinking)", "deepseek-reasoner"),
            ("DeepSeek V3.2", "deepseek-chat"),
            ("Custom model ID", "custom"),
        ],
    },
}


def get_model_options(provider: str, mode: str) -> List[ModelOption]:
    """Return shared model options for a provider and selection mode."""
    return MODEL_OPTIONS[provider.lower()][mode]


def get_known_models() -> Dict[str, List[str]]:
    """Build known model names from the shared CLI catalog."""
    return {
        provider: sorted(
            {
                value
                for options in mode_options.values()
                for _, value in options
            }
        )
        for provider, mode_options in MODEL_OPTIONS.items()
    }


def resolve_provider_from_model(model_name: str) -> Optional[str]:
    """Resolve the provider name from a given model name.

    1. Check if model name exactly matches any known model option in MODEL_OPTIONS.
    2. Check common model name prefixes.
    """
    if not model_name:
        return None

    model_lower = model_name.lower()

    # 1. Exact match against known models in catalog
    known_models = get_known_models()
    for provider, models in known_models.items():
        if model_name in models:
            return provider

    # 2. Check pattern/prefix rules
    if model_lower.startswith("deepseek-"):
        return "deepseek"

    return None


