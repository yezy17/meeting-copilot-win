from __future__ import annotations

import base64
import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
import websockets


class RealtimeTranscriber:
    def __init__(self, api_key: str, model: str, language: str) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language
        self._client = AsyncOpenAI(api_key=api_key)
        self._ws: websockets.ClientConnection | None = None

    async def connect(self) -> None:
        client_secret = await self._client.realtime.client_secrets.create(
            session={
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {
                            "model": self._model,
                            "language": self._language,
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 200,
                            "silence_duration_ms": 260,
                        },
                    }
                },
            }
        )
        self._ws = await websockets.connect(
            "wss://api.openai.com/v1/realtime",
            additional_headers={"Authorization": f"Bearer {client_secret.value}"},
            max_size=None,
            max_queue=64,
        )

    async def append_audio(self, pcm_chunk: bytes) -> None:
        payload = base64.b64encode(pcm_chunk).decode("ascii")
        await self._send({"type": "input_audio_buffer.append", "audio": payload})

    async def commit(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise RuntimeError("Realtime websocket is not connected.")
        async for message in self._ws:
            yield json.loads(message)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Realtime websocket is not connected.")
        await self._ws.send(json.dumps(event))
