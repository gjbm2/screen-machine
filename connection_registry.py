from typing import Dict, Set
import websockets
import asyncio

class ConnectionRegistry:
    """Central place to track active websocket connections."""
    def __init__(self):
        # At most one live audio producer (microphone) per target (e.g. 'devtest')
        self.audio_by_target: Dict[str, websockets.WebSocketServerProtocol] = {}
        # Overlay viewers (read-only)
        self.overlays: Set[websockets.WebSocketServerProtocol] = set()

    # ---------- audio producers ----------
    def attach_audio(self, target: str, ws: websockets.WebSocketServerProtocol):
        """Register an audio websocket for a target, replacing any existing one."""
        current = self.audio_by_target.get(target)
        if current is ws:
            # Same socket registered again – ignore
            return

        if current and current is not ws:
            # Different socket: close the old one (it will trigger handler cleanup)
            try:
                asyncio.create_task(current.close(code=1000, reason="Replaced by new audio connection"))
            except Exception:
                pass

        self.audio_by_target[target] = ws

    def detach_audio(self, target: str, ws: websockets.WebSocketServerProtocol):
        if self.audio_by_target.get(target) is ws:
            self.audio_by_target.pop(target, None)

    def get_audio(self, target: str):
        return self.audio_by_target.get(target)

    # ---------- overlay viewers ----------
    def add_overlay(self, ws: websockets.WebSocketServerProtocol):
        self.overlays.add(ws)

    def remove_overlay(self, ws: websockets.WebSocketServerProtocol):
        self.overlays.discard(ws)

registry = ConnectionRegistry()  # singleton 