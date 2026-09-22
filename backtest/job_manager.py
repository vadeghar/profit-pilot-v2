import queue
import uuid
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class BacktestJob:
    job_id: str
    config: Any
    engine: Any
    event_queue: queue.Queue = field(default_factory=queue.Queue)
    status: str = "pending"
    start_time: datetime = field(default_factory=datetime.utcnow)
    result: Any = None

class BacktestJobManager:
    """Manages active backtest jobs and their event streams."""
    def __init__(self):
        self.jobs: Dict[str, BacktestJob] = {}

    def create_job(self, config: Any, engine: Any) -> str:
        job_id = str(uuid.uuid4())
        job = BacktestJob(job_id=job_id, config=config, engine=engine)
        self.jobs[job_id] = job
        return job_id

    def get_job(self, job_id: str) -> Optional[BacktestJob]:
        return self.jobs.get(job_id)

    def remove_job(self, job_id: str):
        if job_id in self.jobs:
            del self.jobs[job_id]

    def add_event(self, job_id: str, event_type: str, payload: Any):
        if job_id in self.jobs:
            self.jobs[job_id].event_queue.put({
                "event": event_type,
                "payload": payload,
                "timestamp": datetime.utcnow().isoformat()
            })

job_manager = BacktestJobManager()
