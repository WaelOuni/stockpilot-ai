from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class AssistantRequest(ChatRequest):
    thread_id: str


class ChatResponse(BaseModel):
    response: str
