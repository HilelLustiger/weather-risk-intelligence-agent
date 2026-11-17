from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.usage import UsageLimits
from models import AgentAnswer, ChatMessage, ChatRequest, ChatResponse, ConversationDetail, ConversationSummary
from agent import agent

router = APIRouter()

@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    deps = request.state.deps
    history = await deps.db.load_conversation(req.session_id)
    result = await agent.run(req.message, message_history=history, deps=deps, usage_limits=UsageLimits(request_limit=10))
    await deps.db.save_conversation(req.session_id, result.new_messages(), title=req.message[:80])
    return ChatResponse(session_id=req.session_id, answer=result.output)


@router.get("/conversations")
async def list_conversations(request: Request) -> list[ConversationSummary]:
    return await request.state.deps.db.list_conversations()


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> ConversationDetail:
    messages = await request.state.deps.db.load_conversation(conversation_id)
    if not messages:
        raise HTTPException(404, "conversation not found")
    return ConversationDetail(id=conversation_id, messages=_to_chat(messages))


def _to_chat(messages: list[ModelMessage]) -> list[ChatMessage]:
    """Keeps only what the user sees: their questions and the agent's final answers.
    The answer is an AgentAnswer, stored either as JSON text ({"answer": ...}) or as a
    `final_result` tool call, depending on the output mode; tool calls are skipped."""
    chat = []
    for m in messages:
        for p in m.parts:
            if isinstance(m, ModelRequest) and isinstance(p, UserPromptPart) and isinstance(p.content, str):
                chat.append(ChatMessage(role="user", content=p.content))
            elif isinstance(m, ModelResponse) and isinstance(p, ToolCallPart) and p.tool_name == "final_result":
                chat.append(ChatMessage(role="assistant", content=p.args_as_dict().get("answer", "")))
            elif isinstance(m, ModelResponse) and isinstance(p, TextPart):
                try:
                    chat.append(ChatMessage(role="assistant", content=AgentAnswer.model_validate_json(p.content).answer))
                except ValidationError:
                    pass  # interim text, not the final answer
    return chat
