import uuid
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active_by_session: dict[uuid.UUID, dict[str, WebSocket]] = defaultdict(dict)

    async def connect(self, websocket: WebSocket, session_id: uuid.UUID, connection_id: str) -> None:
        await websocket.accept()
        self.active_by_session[session_id][connection_id] = websocket

    def disconnect(self, session_id: uuid.UUID, connection_id: str) -> None:
        self.active_by_session.get(session_id, {}).pop(connection_id, None)
        if not self.active_by_session.get(session_id):
            self.active_by_session.pop(session_id, None)


manager = ConnectionManager()
