"""Provider-neutral configured deployments and immutable turn snapshots."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Backend:
    """Immutable deployment description; voices are IDs accepted by that deployment.

    This is server configuration, not a client-supplied endpoint or credential."""
    id: str
    kind: str
    adapter: str
    model: str
    host: str
    port: int
    voices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    """Freeze a Standard turn's STT/TTS deployments and selected TTS voice.

    Later settings changes do not mutate a selection already held by a turn."""
    stt: Backend
    tts: Backend
    voice_id: str
