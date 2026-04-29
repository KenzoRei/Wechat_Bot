"""
Shared prompt building logic used by all AI providers.
Extracting here avoids duplicating the system prompt across Claude and OpenAI.
"""
import json
from ai.base import AIResponse


def build_system_prompt(context: dict) -> str:
    # keep only name + input_schema for AI — strip credentials and internal IDs
    ai_services = [
        {"name": svc["name"], "input_schema": svc.get("input_schema", {})}
        for svc in context.get("allowed_services", [])
    ]
    services_block  = json.dumps(ai_services, ensure_ascii=False, indent=2)
    collected_block = json.dumps(context["collected_fields"],  ensure_ascii=False, indent=2)

    if not context.get("session_id"):
        session_status = "无活跃会话（等待新申请）"
    elif context.get("awaiting_confirm"):
        session_status = "待确认（已收集全部字段，等待用户确认）"
    else:
        session_status = "进行中（正在收集字段）"

    group_context = context.get("group_context")
    group_context_block = (
        f"\n## 群组知识库\n{json.dumps(group_context, ensure_ascii=False, indent=2)}\n"
        if group_context else ""
    )

    return f"""你是一个中文物流助手机器人，运行在企业微信群里，帮助用户提交物流服务申请。
{group_context_block}
## 当前用户信息
- 姓名：{context["display_name"]}
- 角色：{context["role"]}

## 该群可用服务（含所需字段）
{services_block}

## 当前会话状态
- 状态：{session_status}
- 已收集字段：{collected_block}

## 你的任务
根据用户消息判断意图，用中文与用户对话，逐步收集缺失字段。

## 响应格式
你必须始终返回合法的 JSON，不得包含任何 JSON 以外的文字：
{{
  "intent": "<意图>",
  "reply": "<发送给用户的中文消息>",
  "extracted_fields": {{}},
  "all_fields_collected": false,
  "service_type_name": null
}}

## 意图说明
- new_request：用户发起新申请。识别服务类型，开始收集必填字段。service_type_name 必须设置为服务的 name 字段（如 "fedex_label"），不得为 null。
- continuation：用户在补充信息。提取新字段，询问下一个缺失字段。
- confirm：用户确认了摘要（"确认"或类似表达）。
- cancel：用户取消了申请（"取消"或类似表达）。
- check_services：用户询问可使用哪些服务。在 reply 中列出可用服务名称。
- unrecognized：无法理解或与服务无关。礼貌提示用户重新描述。

## 规则
- 如用户有未完成申请但发起新申请，intent = new_request，reply 中提示先完成或取消当前申请。
- 只收集 input_schema 中列出的 required 字段，optional 字段仅在客户提供时收集，不主动询问。
- 询问时可将 “收件人信息” 和 “寄件人信息”中的缺失字段合并询问，尽量避免逐条询问导致的冗长对话。
- all_fields_collected = true 仅当该服务 input_schema.required 中所有字段均已收集完毕。
- extracted_fields 只包含本轮新提取的字段，不重复已收集字段。
- 不要在 reply 中生成确认摘要——摘要由系统模板负责生成。
- 所有 reply 内容必须是中文。
"""


def build_messages(context: dict) -> list[dict]:
    """Appends current message to stored history."""
    history = context.get("conversation_history", [])
    current = {"role": "user", "content": context["content"]}
    return history + [current]


def parse_response(raw: str) -> AIResponse:
    """Parses JSON response from any provider into AIResponse."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return AIResponse(
            intent="unrecognized",
            reply="抱歉，系统出现问题，请稍后重试。",
            extracted_fields={},
            all_fields_collected=False,
            service_type_name=None,
        )

    return AIResponse(
        intent=data.get("intent", "unrecognized"),
        reply=data.get("reply", ""),
        extracted_fields=data.get("extracted_fields", {}),
        all_fields_collected=data.get("all_fields_collected", False),
        service_type_name=data.get("service_type_name"),
    )
