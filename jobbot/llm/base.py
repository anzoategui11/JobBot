"""Backend interface. Each AI service is one adapter implementing this protocol."""

from typing import Protocol

from ..model import DraftBatch, DraftRequest, MapRequest, MapResult


class Backend(Protocol):
    async def map_fields(self, req: MapRequest) -> MapResult: ...

    async def draft_answer(self, req: DraftRequest) -> str: ...

    async def draft_answers(self, batch: DraftBatch) -> dict[str, str]:
        """Answer several open questions in one request: {field id: draft}."""
        ...

    async def extract(self, system: str, prompt: str, schema: dict, name: str) -> dict:
        """Generic structured extraction with the draft (higher quality) model. Output is unvalidated."""
        ...


class LLMError(RuntimeError):
    """The backend failed or returned something unusable."""
