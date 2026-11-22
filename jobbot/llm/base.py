"""Backend interface. Each AI service is one adapter implementing this protocol."""

from typing import Protocol

from ..model import DraftRequest, MapRequest, MapResult


class Backend(Protocol):
    async def map_fields(self, req: MapRequest) -> MapResult: ...

    async def draft_answer(self, req: DraftRequest) -> str: ...


class LLMError(RuntimeError):
    """The backend failed or returned something unusable."""
