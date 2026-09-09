from dataclasses import dataclass
from datetime import datetime

from .hypothesis import Hypothesis


@dataclass(frozen=True)
class Experiment:
    name: str
    hypothesis: Hypothesis
    started_at: datetime
