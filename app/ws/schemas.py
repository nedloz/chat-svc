import uuid

from pydantic import BaseModel, Field


class WsQuestion(BaseModel):
    type: str = Field(pattern='^question$')
    # Строка, а не UUID: это корреляционный идентификатор, который задаёт клиент, и его формат
    # мы не диктуем. Фронтенд шлёт `req-<время>-<случайное>`, graph-rag-svc принимает `str`,
    # в rag.retrieval_runs колонка TEXT, а WsCancel ниже тоже объявляет строку. Требование UUID
    # стояло только здесь и рвало соединение на любом вопросе: ошибка валидации выбрасывалась
    # из цикла обработки и закрывала WebSocket целиком.
    request_id: str | None = None
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


class WsCancel(BaseModel):
    """Остановка генерации, запущенной этим же клиентом."""

    type: str = Field(pattern='^cancel$')
    request_id: str
