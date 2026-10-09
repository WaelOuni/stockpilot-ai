from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeResult:
    content: str
    source: str | None
    distance: float