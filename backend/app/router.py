import os
from functools import lru_cache

LABELS = ['general', 'coding', 'mathematics', 'reasoning', 'complex analysis']
EXAMPLES = [
    'Everyday questions and general knowledge',
    'Python programming debugging and writing code',
    'Calculate equations algebra geometry arithmetic',
    'Logic puzzles and deductive reasoning',
    'Analyze multiple documents and complex strategic tradeoffs',
]


@lru_cache(maxsize=1)
def encoder():
    if os.getenv('USE_QWEN', 'false').lower() != 'true':
        return None
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(
        os.getenv('QWEN_MODEL', 'Qwen/Qwen3-Embedding-0.6B'),
        device='cpu',
        trust_remote_code=False,
    )


def classify(message):
    try:
        model = encoder()
        if model:
            import numpy as np
            vectors = model.encode(EXAMPLES + [message], normalize_embeddings=True)
            return LABELS[int(np.argmax(vectors[:-1] @ vectors[-1]))]
    except Exception:
        pass
    m = message.lower()
    if any(x in m for x in ['python', 'javascript', 'function', 'debug', 'code', 'traceback', 'fastapi']):
        return 'coding'
    if any(x in m for x in ['equation', 'integral', 'calculate', 'algebra', 'derivative']):
        return 'mathematics'
    if any(x in m for x in ['analyze', 'compare in depth', 'tradeoff', 'architecture']):
        return 'complex analysis'
    if any(x in m for x in ['logic', 'reason', 'prove', 'puzzle']):
        return 'reasoning'
    return 'general'


PREFERENCES = {
    'general': ['groq', 'gemini', 'openai', 'anthropic'],
    'coding': ['openai', 'anthropic', 'gemini', 'groq'],
    'mathematics': ['openai', 'anthropic', 'gemini', 'groq'],
    'reasoning': ['anthropic', 'openai', 'gemini', 'groq'],
    'complex analysis': ['anthropic', 'openai', 'gemini', 'groq'],
}


def select_provider(category, requested='auto', available=None):
    if requested != 'auto':
        return requested
    available = set(available or [])
    for provider in PREFERENCES.get(category, ['gemini']):
        if provider in available:
            return provider
    return 'gemini'