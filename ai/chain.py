from ai.base import AIProvider, AIResponse


class AIProviderChain:
    """
    Tries each provider in order. Moves to the next on any exception.
    If all providers fail, raises RuntimeError — caller marks session failed.
    """

    def __init__(self, providers: list[AIProvider]):
        self.providers = providers  # ordered: primary first, fallbacks after

    def process(self, context: dict) -> AIResponse:
        last_error = None
        for provider in self.providers:
            try:
                return provider.process(context)
            except Exception as e:
                last_error = e
                # TODO: replace this print with structured logging.
                print(f"[ai_chain] {provider.name} failed: {e}")
                continue
        raise RuntimeError(f"All AI providers failed. Last error: {last_error}")


def build_default_chain() -> AIProviderChain:
    """
    Single construction point; this was previously duplicated in
    api/webhook.py and
    core/kefu_case_adapter.py. Provider order (OpenAI primary, Claude
    fallback) is unchanged from both prior call sites.
    """
    from ai.claude_provider import ClaudeProvider
    from ai.openai_provider import OpenAIProvider

    return AIProviderChain(providers=[
        OpenAIProvider(),
        ClaudeProvider(),
    ])
