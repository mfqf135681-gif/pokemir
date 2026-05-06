"""WebSocket handler for real-time data push (Phase 1: stub)."""


class WebSocketManager:
    """Manages WebSocket connections for real-time HUD updates."""

    def __init__(self):
        self._connections: dict[str, any] = {}

    async def connect(self, client_id: str, websocket):
        self._connections[client_id] = websocket

    async def disconnect(self, client_id: str):
        self._connections.pop(client_id, None)

    async def broadcast(self, message: dict):
        for ws in list(self._connections.values()):
            try:
                await ws.send_json(message)
            except Exception:
                pass
