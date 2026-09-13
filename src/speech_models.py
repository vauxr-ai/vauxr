"""Provider-neutral configured deployments and immutable turn snapshots."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    id: str
    kind: str
    adapter: str
    model: str
    host: str
    port: int
    voices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    stt: Backend
    tts: Backend
    voice_id: str
