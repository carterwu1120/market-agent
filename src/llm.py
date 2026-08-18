"""LLM abstraction layer via LiteLLM — cloud-API fallback path.

Only used when settings.llm_backend != "claude_code" (see src/llm_router.py
and src/llm_claude_code.py for the default, no-API-key backend).
Supports OpenAI, Gemini, and Vertex AI. Switch provider via LLM_PROVIDER
in .env.
"""

from typing import Any
import litellm
from litellm import acompletion
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import settings

litellm.set_verbose = False
litellm.suppress_debug_info = True


def _litellm_model_string() -> str:
    """Return the LiteLLM model identifier for the configured provider."""
    match settings.llm_provider:
        case "openai":
            return settings.llm_model
        case "gemini":
            return f"gemini/{settings.llm_model}"
        case "vertex":
            return f"vertex_ai/{settings.llm_model}"
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")


def _litellm_kwargs() -> dict[str, Any]:
    """Extra kwargs passed to every LiteLLM call."""
    kwargs: dict[str, Any] = {}
    match settings.llm_provider:
        case "openai":
            kwargs["api_key"] = settings.openai_api_key
        case "gemini":
            kwargs["api_key"] = settings.gemini_api_key
        case "vertex":
            kwargs["vertex_project"] = settings.google_cloud_project
            kwargs["vertex_location"] = settings.google_cloud_location
    return kwargs


async def llm_chat(messages: list[dict], temperature: float = 0.3, **kwargs) -> str:
    """Single async chat call. Returns the assistant message content."""
    response = await acompletion(
        model=_litellm_model_string(),
        messages=messages,
        temperature=temperature,
        **_litellm_kwargs(),
        **kwargs,
    )
    return response.choices[0].message.content


def get_langchain_llm(temperature: float = 0.3) -> BaseChatModel:
    """Return a LangChain-compatible LLM for use inside LangGraph agents."""
    match settings.llm_provider:
        case "openai":
            return ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.openai_api_key,
                temperature=temperature,
            )
        case "gemini":
            return ChatGoogleGenerativeAI(
                model=settings.llm_model,
                google_api_key=settings.gemini_api_key,
                temperature=temperature,
            )
        case "vertex":
            import google.auth
            credentials, _ = google.auth.default()
            return ChatGoogleGenerativeAI(
                model=settings.llm_model,
                credentials=credentials,
                temperature=temperature,
            )
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
