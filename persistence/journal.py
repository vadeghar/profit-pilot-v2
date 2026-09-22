"""Persistence layer with JSONL event journal and atomic snapshots"""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from utils import ensure_dir, get_timestamp

# Cross-platform advisory file locking.
# macOS/Linux provide `fcntl`; it does NOT exist on Windows, so we fall back to
# the byte-range `msvcrt.locking` there (see FileLock below).
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore


class EventJournal:
    """Append-only event journal using JSONL format"""
    
    def __init__(self, journal_path: str):
        self.journal_path = Path(journal_path)
        ensure_dir(str(self.journal_path.parent))
        self._lock = threading.Lock()
        self._file_handle: Optional[Any] = None
    
    def _get_file_handle(self) -> Any:
        if self._file_handle is None:
            self._file_handle = open(self.journal_path, 'a')
        return self._file_handle
    
    def append(self, event: Dict[str, Any]) -> None:
        """Append an event to the journal"""
        event['_timestamp'] = get_timestamp()
        with self._lock:
            fh = self._get_file_handle()
            fh.write(json.dumps(event) + '\n')
            fh.flush()
    
    def read_all(self) -> List[Dict[str, Any]]:
        """Read all events from the journal"""
        if not self.journal_path.exists():
            return []
        
        events = []
        with open(self.journal_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events
    
    def read_from(self, start_timestamp: str) -> List[Dict[str, Any]]:
        """Read events from a specific timestamp"""
        events = []
        for event in self.read_all():
            if event.get('_timestamp', '') >= start_timestamp:
                events.append(event)
        return events
    
    def close(self) -> None:
        """Close the file handle"""
        with self._lock:
            if self._file_handle:
                self._file_handle.close()
                self._file_handle = None
    
    def rotate(self, max_files: int = 100) -> None:
        """Rotate journal files if they exceed max count"""
        journal_dir = self.journal_path.parent
        journal_name = self.journal_path.stem
        journal_ext = self.journal_path.suffix
        
        # Find existing rotated files
        existing = sorted(
            journal_dir.glob(f"{journal_name}*{journal_ext}"),
            key=lambda p: p.stat().st_mtime
        )
        
        # Rotate if needed
        if len(existing) >= max_files:
            # Remove oldest
            oldest = existing[0]
            oldest.unlink()
            
            # Rename current to numbered
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
            new_name = f"{journal_name}_{timestamp}{journal_ext}"
            self.journal_path.rename(journal_dir / new_name)
            
            # Create new empty journal
            self._file_handle = None


class AtomicStateSnapshot:
    """Atomic state snapshot with temporary file pattern"""
    
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        ensure_dir(str(self.base_path.parent))
        self._lock = threading.Lock()
    
    def save(self, state: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        """Save state atomically using temp file + rename"""
        temp_path = self.base_path.with_suffix('.tmp')
        final_path = self.base_path
        
        snapshot = {
            '_version': 1,
            '_saved_at': get_timestamp(),
            '_metadata': metadata or {},
            '_state': state
        }
        
        with self._lock:
            with open(temp_path, 'w') as f:
                json.dump(snapshot, f, indent=2, default=str)
            
            # Atomic rename
            if final_path.exists():
                final_path.unlink()
            temp_path.rename(final_path)
    
    def load(self) -> Optional[Dict[str, Any]]:
        """Load state from snapshot"""
        if not self.base_path.exists():
            return None
        
        with self._lock:
            with open(self.base_path, 'r') as f:
                data = json.load(f)
                return data.get('_state')
    
    def exists(self) -> bool:
        return self.base_path.exists()


class FileLock:
    """Context manager for file-based locking.

    Uses ``fcntl.flock`` on POSIX (macOS/Linux) and ``msvcrt.locking`` on
    Windows, so the platform runs on both Unix and Windows.
    """

    def __init__(self, lock_path: str):
        self.lock_path = Path(lock_path)
        self._lock_file: Optional[Any] = None

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire lock"""
        ensure_dir(str(self.lock_path.parent))
        # On Windows, open in append mode so we never truncate a file that may
        # be region-locked by another handle; msvcrt.locking is byte-range based
        # and Windows locks are mandatory (no writes allowed into locked bytes).
        mode = 'a+b' if (fcntl is None and msvcrt is not None) else 'w'
        self._lock_file = open(self.lock_path, mode)
        try:
            if fcntl is not None:
                # POSIX advisory lock
                fcntl.flock(self._lock_file.fileno(),
                            fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                return True
            if msvcrt is not None:
                return self._acquire_msvcrt(blocking)
            # No OS locking primitive on this platform -> best-effort no-op.
            return True
        except BlockingIOError:
            self._lock_file.close()
            self._lock_file = None
            return False

    def _acquire_msvcrt(self, blocking: bool) -> bool:
        """Windows byte-range lock (msvcrt.locking) on a single byte.

        No writes are made to the file: an empty file lock is obtained by
        locking 1 byte at offset 0 (LockFileEx permits locking beyond EOF).
        """
        import time as _time
        self._lock_file.seek(0)
        while True:
            try:
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if not blocking:
                    self._lock_file.close()
                    self._lock_file = None
                    return False
                _time.sleep(0.05)

    def release(self) -> None:
        """Release lock"""
        if self._lock_file:
            if fcntl is not None:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            self._lock_file.close()
            self._lock_file = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class JournalWriter:
    """Thread-safe journal writer with event filtering"""
    
    def __init__(self, journal_dir: str, max_files: int = 100):
        self.journal_dir = Path(journal_dir)
        ensure_dir(str(self.journal_dir))
        self.max_files = max_files
        self.journals: Dict[str, EventJournal] = {}
        self._lock = threading.Lock()
    
    def get_journal(self, name: str) -> EventJournal:
        """Get or create a journal by name"""
        with self._lock:
            if name not in self.journals:
                journal_path = self.journal_dir / f"{name}.jsonl"
                self.journals[name] = EventJournal(str(journal_path))
            return self.journals[name]
    
    def write_event(self, journal_name: str, event_type: str, data: Dict[str, Any]) -> None:
        """Write an event to a journal"""
        event = {
            '_type': event_type,
            **data
        }
        journal = self.get_journal(journal_name)
        journal.append(event)
        
        # Check rotation
        if journal.journal_path.exists():
            size = journal.journal_path.stat().st_size
            if size > 10 * 1024 * 1024:  # 10MB
                journal.rotate(self.max_files)
    
    def close_all(self) -> None:
        """Close all journals"""
        with self._lock:
            for journal in self.journals.values():
                journal.close()
            self.journals.clear()


class StateStore:
    """State store with journal + snapshot"""
    
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        ensure_dir(str(self.state_dir))
        self.journal_writer = JournalWriter(str(self.state_dir / 'journals'))
        self._lock = threading.Lock()
    
    def save_state(self, key: str, state: Dict[str, Any], 
                   journal: bool = True, snapshot: bool = True) -> None:
        """Save state to journal and/or snapshot"""
        with self._lock:
            if journal:
                self.journal_writer.write_event('state', f'STATE_{key.upper()}', {
                    'key': key,
                    'state': state
                })
            
            if snapshot:
                snapshot_path = self.state_dir / f'{key}.snapshot.json'
                AtomicStateSnapshot(str(snapshot_path)).save(state, {'key': key})
    
    def load_state(self, key: str) -> Optional[Dict[str, Any]]:
        """Load state from snapshot"""
        snapshot_path = self.state_dir / f'{key}.snapshot.json'
        return AtomicStateSnapshot(str(snapshot_path)).load()
    
    def has_state(self, key: str) -> bool:
        """Check if state exists"""
        snapshot_path = self.state_dir / f'{key}.snapshot.json'
        return snapshot_path.exists()
    
    def close(self) -> None:
        """Close the state store"""
        self.journal_writer.close_all()
    
    def replay_events(self, journal_name: str, handler: Callable[[Dict[str, Any]], None]) -> None:
        """Replay events from a journal"""
        journal = self.journal_writer.get_journal(journal_name)
        for event in journal.read_all():
            handler(event)
    
    def replay_from_timestamp(self, journal_name: str, timestamp: str, 
                              handler: Callable[[Dict[str, Any]], None]) -> None:
        """Replay events from a specific timestamp"""
        journal = self.journal_writer.get_journal(journal_name)
        for event in journal.read_from(timestamp):
            handler(event)
