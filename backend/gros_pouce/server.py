from __future__ import annotations

import json
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .media import MediaError, ensure_ffmpeg, extract_audio_to_wav
from .parakeet import BackendUnavailable, create_backend
from .subtitles import (
    SubtitleOptions,
    build_cues_from_segments,
    build_cues_from_words,
    cues_to_srt,
)


app = FastAPI(title="Gros Pouce Parakeet Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SubtitleSettings(BaseModel):
    max_line_chars: int = Field(default=42, ge=20, le=60)
    max_lines: int = Field(default=2, ge=1, le=3)
    min_duration: float = Field(default=0.75, ge=0.2, le=2.5)
    max_duration: float = Field(default=5.5, ge=1.0, le=10.0)
    max_cps: float = Field(default=18.0, ge=8.0, le=30.0)
    pause_break: float = Field(default=0.45, ge=0.1, le=1.5)


class JobRequest(BaseModel):
    media_path: str
    output_dir: str | None = None
    backend: str = "auto"
    model_id: str | None = None
    trim_start_seconds: float | None = None
    trim_end_seconds: float | None = None
    chunk_duration: float = Field(default=120.0, ge=0.0, le=1800.0)
    overlap_duration: float = Field(default=15.0, ge=0.0, le=120.0)
    subtitle_settings: SubtitleSettings = Field(default_factory=SubtitleSettings)


class JobResponse(BaseModel):
    job_id: str


_executor = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_backend_lock = threading.Lock()
_backend_cache: dict[tuple[str, str | None], Any] = {}


def _set_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        _jobs[job_id].update(updates)


def _get_backend(preferred: str, model_id: str | None):
    key = ((preferred or "auto").lower(), model_id)
    with _backend_lock:
        if key not in _backend_cache:
            _backend_cache[key] = create_backend(preferred=key[0], model_id=model_id)
        return _backend_cache[key]


def _output_paths(media_path: str, output_dir: str | None) -> tuple[Path, Path]:
    media = Path(media_path)
    destination = Path(output_dir) if output_dir else media.parent
    destination.mkdir(parents=True, exist_ok=True)
    base = media.stem + ".parakeet"
    return destination / f"{base}.srt", destination / f"{base}.json"


def _run_job(job_id: str, request: JobRequest) -> None:
    try:
        media_path = Path(request.media_path)
        if not media_path.exists():
            raise MediaError(f"Fichier media introuvable: {request.media_path}")

        _set_job(job_id, status="running", step="Extraction audio")
        srt_path, json_path = _output_paths(request.media_path, request.output_dir)

        with tempfile.TemporaryDirectory(prefix="gros-pouce-") as tmpdir:
            wav_path = str(Path(tmpdir) / "audio.wav")
            extract_audio_to_wav(
                request.media_path,
                wav_path,
                trim_start_seconds=request.trim_start_seconds,
                trim_end_seconds=request.trim_end_seconds,
            )

            _set_job(job_id, step="Chargement/transcription Parakeet")
            backend = _get_backend(request.backend, request.model_id)
            result = backend.transcribe(
                wav_path,
                chunk_duration=request.chunk_duration,
                overlap_duration=request.overlap_duration,
            )

        _set_job(job_id, step="Generation SRT")
        options = SubtitleOptions(**request.subtitle_settings.model_dump())
        cues = build_cues_from_words(result.words, options)
        if not cues:
            cues = build_cues_from_segments(result.segments, options)
        if not cues:
            raise RuntimeError("Parakeet n'a retourne aucun timestamp exploitable.")

        srt_text = cues_to_srt(cues)
        srt_path.write_text(srt_text, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "text": result.text,
                    "backend": result.backend,
                    "model_id": result.model_id,
                    "cue_count": len(cues),
                    "srt_path": str(srt_path),
                    "words": [word.__dict__ for word in result.words],
                    "segments": [segment.__dict__ for segment in result.segments],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _set_job(
            job_id,
            status="done",
            step="Termine",
            result={
                "srt_path": str(srt_path),
                "json_path": str(json_path),
                "cue_count": len(cues),
                "backend": result.backend,
                "model_id": result.model_id,
            },
        )
    except Exception as exc:
        _set_job(job_id, status="error", step="Erreur", error=str(exc))


@app.get("/health")
def health() -> dict[str, Any]:
    ffmpeg_ok = True
    ffmpeg_error = None
    try:
        ffmpeg_path = ensure_ffmpeg()
    except Exception as exc:
        ffmpeg_ok = False
        ffmpeg_path = None
        ffmpeg_error = str(exc)

    return {
        "ok": True,
        "service": "gros-pouce-parakeet",
        "ffmpeg_ok": ffmpeg_ok,
        "ffmpeg_path": ffmpeg_path,
        "ffmpeg_error": ffmpeg_error,
        "loaded_backends": [f"{backend.name}:{backend.model_id}" for backend in _backend_cache.values()],
    }


@app.post("/jobs", response_model=JobResponse)
def create_job(request: JobRequest) -> JobResponse:
    if not Path(request.media_path).exists():
        raise HTTPException(status_code=400, detail=f"Fichier introuvable: {request.media_path}")

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "En attente",
            "result": None,
            "error": None,
        }
    _executor.submit(_run_job, job_id, request)
    return JobResponse(job_id=job_id)


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job introuvable")
        return dict(job)


@app.post("/transcribe")
def transcribe_sync(request: JobRequest) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "En attente",
            "result": None,
            "error": None,
        }
    _run_job(job_id, request)
    job = get_job(job_id)
    if job["status"] == "error":
        raise HTTPException(status_code=500, detail=job["error"])
    return job
