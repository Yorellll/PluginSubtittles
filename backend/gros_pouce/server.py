from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .media import MediaError, ensure_ffmpeg, extract_audio_to_wav, get_wav_duration_seconds
from .parakeet import BackendUnavailable, create_backend, unload_backend_instance
from .subtitles import (
    SourceCue,
    SubtitleOptions,
    WordStamp,
    build_cues_from_segments,
    build_cues_from_words,
    cues_to_srt,
    shift_cues,
    source_cues_to_subtitle_cues,
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


class SourceItem(BaseModel):
    clip_key: str
    source_label: str
    media_path: str
    timeline_offset_seconds: float = 0.0
    trim_start_seconds: float | None = None
    trim_end_seconds: float | None = None


class BatchJobRequest(BaseModel):
    aggregate_key: str
    aggregate_label: str
    output_dir: str
    backend: str = "auto"
    model_id: str | None = None
    chunk_duration: float = Field(default=120.0, ge=0.0, le=1800.0)
    overlap_duration: float = Field(default=15.0, ge=0.0, le=120.0)
    subtitle_settings: SubtitleSettings = Field(default_factory=SubtitleSettings)
    source_items: list[SourceItem]


class BackendSelectionRequest(BaseModel):
    backend: str = "nemo"
    preload: bool = True


class JobResponse(BaseModel):
    job_id: str


_executor = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_backend_lock = threading.Lock()
_backend_cache: dict[tuple[str, str | None], Any] = {}
_selected_backend_name = "nemo"


def _set_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        _jobs[job_id].update(updates)


def _get_backend(preferred: str, model_id: str | None):
    requested = (preferred or "auto").lower()
    if requested == "auto":
        requested = _selected_backend_name
    key = (requested, model_id)
    with _backend_lock:
        stale_keys = [cache_key for cache_key in _backend_cache if cache_key != key]
        for stale_key in stale_keys:
            unload_backend_instance(_backend_cache.pop(stale_key, None))
        if key not in _backend_cache:
            _backend_cache[key] = create_backend(preferred=key[0], model_id=model_id)
        return _backend_cache[key]


def _select_backend(backend_name: str, preload: bool) -> dict[str, Any]:
    global _selected_backend_name
    normalized = (backend_name or "nemo").lower()
    if normalized not in ("nemo", "whisper", "mlx", "auto"):
        raise HTTPException(status_code=400, detail=f"Backend inconnu: {backend_name}")
    if normalized == "auto":
        normalized = "nemo"

    with _backend_lock:
        _selected_backend_name = normalized
        for cache_key in list(_backend_cache.keys()):
            unload_backend_instance(_backend_cache.pop(cache_key, None))
        if preload:
            _backend_cache[(normalized, None)] = create_backend(preferred=normalized, model_id=None)

    return {
        "selected_backend": _selected_backend_name,
        "loaded_backends": [f"{backend.name}:{backend.model_id}" for backend in _backend_cache.values()],
    }


def _output_paths(media_path: str, output_dir: str | None) -> tuple[Path, Path]:
    media = Path(media_path)
    destination = Path(output_dir) if output_dir else media.parent
    destination.mkdir(parents=True, exist_ok=True)
    base = media.stem + ".parakeet"
    return destination / f"{base}.srt", destination / f"{base}.json"


def _aggregate_paths(output_dir: str, aggregate_label: str) -> tuple[Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in aggregate_label).strip("_")
    if not safe_label:
        safe_label = "sequence"
    base = f"{safe_label}.parakeet"
    return destination / f"{base}.srt", destination / f"{base}.json"


def _build_cues_from_result(result: Any, options: SubtitleOptions) -> list[Any]:
    cues = build_cues_from_words(result.words, options)
    if not cues:
        cues = build_cues_from_segments(result.segments, options)
    if not cues:
        raise RuntimeError("Parakeet n'a retourne aucun timestamp exploitable.")
    return cues


def _fallback_segment_from_text(text: str, audio_duration_seconds: float) -> list[Any]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    duration = max(0.75, float(audio_duration_seconds or 0.0))
    return [
        {
            "text": cleaned,
            "start": 0.0,
            "end": duration,
        }
    ]


def _transcribe_source_item(
    backend: Any,
    source_item: SourceItem,
    request_backend: str,
    request_model_id: str | None,
    chunk_duration: float,
    overlap_duration: float,
    options: SubtitleOptions,
) -> tuple[list[SourceCue], dict[str, Any]]:
    media_path = Path(source_item.media_path)
    if not media_path.exists():
        raise MediaError(f"Fichier media introuvable: {source_item.media_path}")

    with tempfile.TemporaryDirectory(prefix="gros-pouce-") as tmpdir:
        wav_path = str(Path(tmpdir) / "audio.wav")
        extract_audio_to_wav(
            source_item.media_path,
            wav_path,
            trim_start_seconds=source_item.trim_start_seconds,
            trim_end_seconds=source_item.trim_end_seconds,
        )
        wav_duration_seconds = get_wav_duration_seconds(wav_path)
        result = backend.transcribe(
            wav_path,
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
        )

    skipped = False
    try:
        cues = _build_cues_from_result(result, options)
    except RuntimeError:
        fallback_segments = _fallback_segment_from_text(getattr(result, "text", ""), wav_duration_seconds)
        if not fallback_segments:
            skipped = True
            cues = []
        else:
            cues = build_cues_from_segments(
                [
                    WordStamp(
                        start=float(segment["start"]),
                        end=float(segment["end"]),
                        text=str(segment["text"]),
                    )
                    for segment in fallback_segments
                ],
                options,
            )
    shifted = shift_cues(
        cues,
        source_item.timeline_offset_seconds,
        source_item.clip_key,
        source_item.source_label,
    )
    metadata = {
        "clip_key": source_item.clip_key,
        "source_label": source_item.source_label,
        "media_path": source_item.media_path,
        "timeline_offset_seconds": source_item.timeline_offset_seconds,
        "trim_start_seconds": source_item.trim_start_seconds,
        "trim_end_seconds": source_item.trim_end_seconds,
        "backend": result.backend if hasattr(result, "backend") else request_backend,
        "model_id": result.model_id if hasattr(result, "model_id") else request_model_id,
        "text": getattr(result, "text", ""),
        "cue_count": len(shifted),
        "skipped": skipped,
        "words": [word.__dict__ for word in getattr(result, "words", [])],
        "segments": [segment.__dict__ for segment in getattr(result, "segments", [])],
    }
    return shifted, metadata


def _load_existing_aggregate(json_path: Path) -> dict[str, Any]:
    if not json_path.exists():
        return {"clips": [], "cues": []}
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return {"clips": [], "cues": []}
    payload.setdefault("clips", [])
    payload.setdefault("cues", [])
    return payload


def _merge_aggregate(
    json_path: Path,
    srt_path: Path,
    aggregate_key: str,
    aggregate_label: str,
    new_cues: list[SourceCue],
    clip_metadata: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _load_existing_aggregate(json_path)
    replaced_keys = {meta["clip_key"] for meta in clip_metadata}

    kept_cues = []
    for cue_data in payload.get("cues", []):
        if cue_data.get("clip_key") not in replaced_keys:
            kept_cues.append(cue_data)

    kept_clips = []
    for clip_data in payload.get("clips", []):
        if clip_data.get("clip_key") not in replaced_keys:
            kept_clips.append(clip_data)

    merged_cues = kept_cues + [asdict(cue) for cue in new_cues]
    merged_clips = kept_clips + clip_metadata
    deduped_cues: list[dict[str, Any]] = []
    seen_cues: set[tuple[str, float, float, str]] = set()
    for cue in merged_cues:
        cue_key = (
            str(cue["clip_key"]),
            round(float(cue["start"]), 3),
            round(float(cue["end"]), 3),
            str(cue["text"]),
        )
        if cue_key in seen_cues:
            continue
        seen_cues.add(cue_key)
        deduped_cues.append(cue)

    source_cues = [
        SourceCue(
            clip_key=str(cue["clip_key"]),
            source_label=str(cue.get("source_label", "")),
            start=float(cue["start"]),
            end=float(cue["end"]),
            text=str(cue["text"]),
        )
        for cue in deduped_cues
    ]
    subtitle_cues = source_cues_to_subtitle_cues(source_cues)
    srt_path.write_text(cues_to_srt(subtitle_cues), encoding="utf-8")

    merged_payload = {
        "aggregate_key": aggregate_key,
        "aggregate_label": aggregate_label,
        "srt_path": str(srt_path),
        "json_path": str(json_path),
        "cue_count": len(subtitle_cues),
        "clip_count": len(merged_clips),
        "clips": sorted(merged_clips, key=lambda clip: float(clip.get("timeline_offset_seconds", 0.0))),
        "cues": [asdict(cue) for cue in source_cues],
    }
    json_path.write_text(
        json.dumps(merged_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return merged_payload


def _run_job(job_id: str, request: JobRequest) -> None:
    try:
        media_path = Path(request.media_path)
        if not media_path.exists():
            raise MediaError(f"Fichier media introuvable: {request.media_path}")

        _set_job(job_id, status="running", step="Extraction audio")
        srt_path, json_path = _output_paths(request.media_path, request.output_dir)

        options = SubtitleOptions(**request.subtitle_settings.model_dump())
        _set_job(job_id, step="Chargement/transcription Parakeet")
        backend = _get_backend(request.backend, request.model_id)
        source_item = SourceItem(
            clip_key=Path(request.media_path).stem,
            source_label=Path(request.media_path).name,
            media_path=request.media_path,
            timeline_offset_seconds=0.0,
            trim_start_seconds=request.trim_start_seconds,
            trim_end_seconds=request.trim_end_seconds,
        )
        shifted_cues, metadata = _transcribe_source_item(
            backend,
            source_item,
            request.backend,
            request.model_id,
            request.chunk_duration,
            request.overlap_duration,
            options,
        )

        _set_job(job_id, step="Generation SRT")
        subtitle_cues = source_cues_to_subtitle_cues(shifted_cues)
        srt_text = cues_to_srt(subtitle_cues)
        srt_path.write_text(srt_text, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "text": metadata["text"],
                    "backend": metadata["backend"],
                    "model_id": metadata["model_id"],
                    "cue_count": len(subtitle_cues),
                    "srt_path": str(srt_path),
                    "words": metadata["words"],
                    "segments": metadata["segments"],
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
                "cue_count": len(subtitle_cues),
                "backend": metadata["backend"],
                "model_id": metadata["model_id"],
            },
        )
    except Exception as exc:
        _set_job(job_id, status="error", step="Erreur", error=str(exc))


def _run_batch_job(job_id: str, request: BatchJobRequest) -> None:
    try:
        if not request.source_items:
            raise RuntimeError("Aucun clip source fourni.")

        _set_job(job_id, status="running", step="Chargement/transcription Parakeet")
        options = SubtitleOptions(**request.subtitle_settings.model_dump())
        backend = _get_backend(request.backend, request.model_id)
        all_new_cues: list[SourceCue] = []
        clip_metadata: list[dict[str, Any]] = []
        skipped_clips: list[str] = []

        for index, source_item in enumerate(request.source_items, start=1):
            _set_job(job_id, step=f"Transcription clip {index}/{len(request.source_items)}")
            shifted_cues, metadata = _transcribe_source_item(
                backend,
                source_item,
                request.backend,
                request.model_id,
                request.chunk_duration,
                request.overlap_duration,
                options,
            )
            clip_metadata.append(metadata)
            if metadata.get("skipped"):
                skipped_clips.append(metadata["source_label"])
                continue
            all_new_cues.extend(shifted_cues)

        if not all_new_cues:
            if skipped_clips:
                raise RuntimeError(
                    "Aucun sous-titre exploitable. Clips ignores: " + ", ".join(skipped_clips)
                )
            raise RuntimeError("Aucun sous-titre exploitable.")

        _set_job(job_id, step="Fusion SRT/JSON")
        srt_path, json_path = _aggregate_paths(request.output_dir, request.aggregate_label)
        merged_payload = _merge_aggregate(
            json_path=json_path,
            srt_path=srt_path,
            aggregate_key=request.aggregate_key,
            aggregate_label=request.aggregate_label,
            new_cues=all_new_cues,
            clip_metadata=clip_metadata,
        )
        _set_job(
            job_id,
            status="done",
            step="Termine",
            result={
                "srt_path": merged_payload["srt_path"],
                "json_path": merged_payload["json_path"],
                "cue_count": merged_payload["cue_count"],
                "clip_count": merged_payload["clip_count"],
                "backend": request.backend,
                "model_id": request.model_id,
                "skipped_clips": skipped_clips,
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
        "selected_backend": _selected_backend_name,
        "available_backends": ["nemo", "whisper"],
        "loaded_backends": [f"{backend.name}:{backend.model_id}" for backend in _backend_cache.values()],
    }


@app.post("/backend/select")
def select_backend(request: BackendSelectionRequest) -> dict[str, Any]:
    return _select_backend(request.backend, request.preload)


@app.post("/backend/unload")
def unload_backend() -> dict[str, Any]:
    with _backend_lock:
        for cache_key in list(_backend_cache.keys()):
            unload_backend_instance(_backend_cache.pop(cache_key, None))
    return {
        "selected_backend": _selected_backend_name,
        "loaded_backends": [],
    }


@app.post("/shutdown")
def shutdown_service() -> dict[str, Any]:
    threading.Timer(0.25, lambda: os._exit(0)).start()
    return {"ok": True, "message": "Arret du service demande."}


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


@app.post("/batch-jobs", response_model=JobResponse)
def create_batch_job(request: BatchJobRequest) -> JobResponse:
    if not request.source_items:
        raise HTTPException(status_code=400, detail="Aucun clip source fourni.")
    for item in request.source_items:
        if not Path(item.media_path).exists():
            raise HTTPException(status_code=400, detail=f"Fichier introuvable: {item.media_path}")

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "En attente",
            "result": None,
            "error": None,
        }
    _executor.submit(_run_batch_job, job_id, request)
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
