from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=32000)


class AssistantRequest(ChatRequest):
    thread_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_identity(cls, value):
        # Existing clients may send this inert field. Identity continues to
        # come exclusively from the verified JWT; all other extras are refused.
        if isinstance(value, dict) and "customer_id" in value:
            return {key: item for key, item in value.items() if key != "customer_id"}
        return value


class ChatResponse(BaseModel):
    response: str
