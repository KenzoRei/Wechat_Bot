from openai import OpenAI
from ai.base import AIProvider, AIResponse
from ai.prompt_builder import build_system_prompt, build_messages, parse_response
import config

client = OpenAI(api_key=config.OPENAI_API_KEY)


class OpenAIProvider(AIProvider):

    @property
    def name(self) -> str:
        return "openai"

    def process(self, context: dict) -> AIResponse:
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": build_system_prompt(context)},
                *build_messages(context),
            ],
            response_format={"type": "json_object"},  # forces valid JSON output
            max_tokens=1024,
        )
        return parse_response(response.choices[0].message.content)
