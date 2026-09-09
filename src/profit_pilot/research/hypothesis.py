from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Hypothesis:
    name: str
    statement: str
    parameters: Mapping[str, object] = field(default_factory=dict)
