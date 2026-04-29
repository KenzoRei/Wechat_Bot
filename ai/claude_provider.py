import anthropic
from ai.base import AIProvider, AIResponse
from ai.prompt_builder import build_system_prompt, build_messages, parse_response
import config

client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)


class ClaudeProvider(AIProvider):

    @property
    def name(self) -> str:
        return "claude"

    def process(self, context: dict) -> AIResponse:
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=build_system_prompt(context),
            messages=build_messages(context),
        )
        return parse_response(response.content[0].text)
