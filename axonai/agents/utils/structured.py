"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _extract_json_objects(text: str) -> list[str]:
    """Extract top-level JSON objects from text using brace-depth scanning."""
    objects = []
    depth = 0
    start = None
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:i + 1])
                start = None
    return objects


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    schema: Optional[type[T]] = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if result is None:
                raise ValueError("structured output returned None")
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    if schema is not None:
        import json
        import re
        try:
            json_instruction = (
                f"\n\nYou must return your response ONLY as a JSON object matching this schema:\n"
                f"{json.dumps(schema.model_json_schema(), indent=2)}\n"
                f"Do not include any conversational explanation, markdown formatting blocks (like ```json), or extra text outside the JSON object itself."
            )
            
            fallback_prompt = None
            if isinstance(prompt, str):
                fallback_prompt = prompt + json_instruction
            elif isinstance(prompt, list):
                fallback_prompt = []
                for msg in prompt:
                    if isinstance(msg, dict) and msg.get("role") == "system":
                        fallback_prompt.append({
                            "role": "system",
                            "content": msg["content"] + json_instruction
                        })
                    else:
                        fallback_prompt.append(msg)
                if len(fallback_prompt) == len(prompt):
                    fallback_prompt.append({
                        "role": "system",
                        "content": json_instruction
                    })
            else:
                fallback_prompt = prompt

            response = plain_llm.invoke(fallback_prompt)
            content = response.content.strip()
            
            # Strip markdown fences
            if "```" in content:
                content = re.sub(r"```(?:json)?\s*", "", content)
                content = re.sub(r"```", "", content)
                content = content.strip()

            # Find all top-level JSON objects via brace-depth scanning
            candidates = _extract_json_objects(content)

            # Filter out schema-definition echoes
            candidates = [c for c in candidates if '"$defs"' not in c and "'$defs'" not in c]

            if not candidates:
                # Last resort: greedy match
                m = re.search(r"(\{.*\})", content, re.DOTALL)
                if m:
                    candidates = [m.group(1)]

            data = None
            for cand in candidates:
                try:
                    data = json.loads(cand)
                    break
                except Exception:
                    # Try cleaning comments / trailing commas
                    cleaned_str = re.sub(r"^\s*//.*$", "", cand, flags=re.MULTILINE)
                    cleaned_str = re.sub(r",\s*([\]}])", r"\1", cleaned_str)
                    try:
                        data = json.loads(cleaned_str)
                        break
                    except Exception:
                        continue

            if data is None:
                raise ValueError("no parseable JSON object found in response")

            parsed_instance = schema(**data)
            return render(parsed_instance)
        except Exception as exc:
            logger.warning(
                "%s: fallback JSON parsing failed (%s); falling back to raw free-text content",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content

