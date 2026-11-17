from __future__ import annotations

import threading
import time

import pytest

from app.config import Settings
from app.runs import RunRecord, RunRegistry
from app.tasks import JobContext

from tests.fakes import InMemoryStorage, RecordingCallbacks


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_base_url="http://ddp.local",
        public_base_url="http://adapter.local",
        training_adapter_id="local-training",
        training_adapter_version="1.0.0",
        training_secret="t" * 40,
        model_adapter_id="local-model",
        model_adapter_version="1.0.0",
        model_secret="m" * 40,
        ffmpeg_path="/nonexistent/ffmpeg",
        ffprobe_path="/nonexistent/ffprobe",
        frames_per_video=2,
        image_size=32,
        max_pending_runs=8,
        callback_max_attempts=2,
        callback_initial_delay_seconds=0.0,
    )


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def callbacks() -> RecordingCallbacks:
    return RecordingCallbacks()


def make_context(
    *,
    run_id: str,
    kind: str,
    storage: InMemoryStorage,
    settings: Settings,
    registry: RunRegistry,
    callbacks: RecordingCallbacks,
    timeout: int = 60,
) -> JobContext:
    registry.create(
        RunRecord(
            run_id=run_id,
            kind=kind,
            adapter_id="local-model" if kind != "training" else "local-training",
            adapter_version="1.0.0",
        )
    )
    return JobContext(
        run_id=run_id,
        kind=kind,
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
        cancel_event=threading.Event(),
        deadline_monotonic=time.monotonic() + timeout,
    )
