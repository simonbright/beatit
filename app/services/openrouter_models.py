"""Curated OpenRouter models for the settings UI."""

DEFAULT_OPENROUTER_MODEL = "google/gemini-3.1-flash-lite"

# Retired on OpenRouter — auto-migrated to DEFAULT_OPENROUTER_MODEL on startup
DEPRECATED_OPENROUTER_MODELS = {
    "google/gemini-2.0-flash-lite-001",
    "google/gemini-2.0-flash-001",
}

OPENROUTER_MODELS = [
    {
        "id": "google/gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash Lite",
        "tier": "budget",
        "description": "Lowest cost — good for summaries and quick queries",
    },
    {
        "id": "meta-llama/llama-3.1-8b-instruct",
        "label": "Llama 3.1 8B",
        "tier": "budget",
        "description": "Inexpensive open model for basic analysis",
    },
    {
        "id": "openai/gpt-4o-mini",
        "label": "GPT-4o Mini",
        "tier": "standard",
        "description": "Balanced cost and quality",
    },
    {
        "id": "google/gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "tier": "standard",
        "description": "Fast, capable general model",
    },
    {
        "id": "anthropic/claude-3.5-sonnet",
        "label": "Claude 3.5 Sonnet",
        "tier": "premium",
        "description": "Strong reasoning for complex case review",
    },
    {
        "id": "openai/gpt-4o",
        "label": "GPT-4o",
        "tier": "premium",
        "description": "Highest quality, higher cost",
    },
]

MODEL_IDS = {m["id"] for m in OPENROUTER_MODELS}


def model_label(model_id: str) -> str:
    for model in OPENROUTER_MODELS:
        if model["id"] == model_id:
            return model["label"]
    return model_id
