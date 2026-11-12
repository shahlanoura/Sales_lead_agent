from django.conf import settings
from langchain_groq import ChatGroq


def get_llm(temperature: float = 0.2):
    extra = {}
    if settings.GROQ_MODEL.startswith("openai/gpt-oss"):
        extra["reasoning_effort"] = "low"  # much faster; these tasks do not need deep reasoning
    return ChatGroq(model=settings.GROQ_MODEL, temperature=temperature,
                    api_key=settings.GROQ_API_KEY, max_retries=5, **extra)


class _Structured:
    """Structured output via Groq's JSON-schema mode (reliable on gpt-oss / llama-4).
    Falls back to tool-calling mode if the model/endpoint rejects the schema mode."""

    def __init__(self, schema):
        llm = get_llm()
        self.primary = llm.with_structured_output(schema, method="json_schema")
        self.fallback = llm.with_structured_output(schema, method="function_calling")

    def invoke(self, messages):
        try:
            return self.primary.invoke(messages)
        except Exception:
            return self.fallback.invoke(messages)


def structured(schema):
    return _Structured(schema)