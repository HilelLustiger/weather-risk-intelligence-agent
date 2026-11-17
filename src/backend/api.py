from fastapi import APIRouter, Request
from pydantic_ai.usage import UsageLimits
from models import ChatRequest, ChatResponse
from agent import agent

router = APIRouter()

@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> ChatResponse:
    deps = request.state.deps
    history = await deps.db.load_conversation(req.session_id)
    result = await agent.run(req.message, message_history=history, deps=deps, usage_limits=UsageLimits(request_limit=10))
    await deps.db.save_conversation(req.session_id, result.new_messages())
    return ChatResponse(session_id=req.session_id, answer=result.output)