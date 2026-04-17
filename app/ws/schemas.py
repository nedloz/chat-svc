import uuid

from pydantic import BaseModel, Field


class WsQuestion(BaseModel):
    type: str = Field(pattern='^question$')
    request_id: uuid.UUID | None = None
    content: str


class WsHistoryGet(BaseModel):
    type: str = Field(pattern='^history_get$')
    limit: int = 20
    before_message_id: uuid.UUID | None = None


class WsFeedbackSet(BaseModel):
    type: str = Field(pattern='^feedback_set$')
    message_id: uuid.UUID
    helpful: int


class WsPing(BaseModel):
    type: str = Field(pattern='^ping$')
