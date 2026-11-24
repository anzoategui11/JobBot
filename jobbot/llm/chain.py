"""Primary backend with an automatic backup: if the primary fails (rate limit, outage, bad output),
the same request goes to the backup. Output is validated by whichever backend answered."""

from ..model import DraftBatch, DraftRequest, MapRequest, MapResult
from .base import LLMError


class Chain:
    def __init__(self, primary, backup):
        self.primary, self.backup = primary, backup
        self._note = ""
        self.last_meta = None

    def take_note(self) -> str:
        """One-line note for the panel when the backup answered (then cleared)."""
        note, self._note = self._note, ""
        return note

    async def _run(self, method: str, *args):
        try:
            out = await getattr(self.primary, method)(*args)
            self.last_meta = getattr(self.primary, "last_meta", None)
            return out
        except LLMError as first:
            try:
                out = await getattr(self.backup, method)(*args)
            except LLMError as second:
                raise LLMError(f"{first} | backup: {second}") from second
            self.last_meta = getattr(self.backup, "last_meta", None)
            name = getattr(self.backup, "NAME", "backup")
            self._note = f"Used the {name} backup ({first})."
            print(f"[backup] {self._note}", flush=True)
            return out

    async def map_fields(self, req: MapRequest) -> MapResult:
        return await self._run("map_fields", req)

    async def draft_answer(self, req: DraftRequest) -> str:
        return await self._run("draft_answer", req)

    async def draft_answers(self, batch: DraftBatch) -> dict[str, str]:
        return await self._run("draft_answers", batch)

    async def extract(self, system: str, prompt: str, schema: dict, name: str) -> dict:
        return await self._run("extract", system, prompt, schema, name)

    async def aclose(self) -> None:
        for b in (self.primary, self.backup):
            if hasattr(b, "aclose"):
                await b.aclose()
