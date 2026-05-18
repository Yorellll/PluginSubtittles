from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Iterable

from .subtitles import WordStamp


NEMO_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"
MLX_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER_MODEL_ID = "large-v3"


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    words: list[WordStamp]
    segments: list[WordStamp]
    backend: str
    model_id: str


class BackendUnavailable(RuntimeError):
    pass


class BaseParakeetBackend:
    name = "base"

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or ""
        self.model: Any = None

    def load(self) -> None:
        raise NotImplementedError

    def transcribe(self, wav_path: str, chunk_duration: float, overlap_duration: float) -> TranscriptionResult:
        raise NotImplementedError

    def unload(self) -> None:
        self.model = None


def _float_attr(value: Any, *names: str) -> float | None:
    for name in names:
        if isinstance(value, dict) and name in value:
            try:
                return float(value[name])
            except (TypeError, ValueError):
                return None
        if hasattr(value, name):
            try:
                return float(getattr(value, name))
            except (TypeError, ValueError):
                return None
    return None


def _text_attr(value: Any, *names: str) -> str:
    for name in names:
        if isinstance(value, dict) and name in value:
            return str(value[name])
        if hasattr(value, name):
            return str(getattr(value, name))
    return ""


def _parse_stamp_list(stamps: Iterable[Any], text_keys: tuple[str, ...]) -> list[WordStamp]:
    parsed: list[WordStamp] = []
    for stamp in stamps or []:
        start = _float_attr(stamp, "start", "start_time")
        end = _float_attr(stamp, "end", "end_time")
        text = _text_attr(stamp, *text_keys)
        if start is None or end is None or not text:
            continue
        parsed.append(WordStamp(text=text, start=start, end=end))
    return parsed


def _parse_nemo_timestamps(hypothesis: Any) -> tuple[list[WordStamp], list[WordStamp]]:
    timestamp_container = (
        getattr(hypothesis, "timestamp", None)
        or getattr(hypothesis, "timestamps", None)
        or getattr(hypothesis, "timestep", None)
        or {}
    )
    if not isinstance(timestamp_container, dict):
        timestamp_container = {}

    words = _parse_stamp_list(
        timestamp_container.get("word", [])
        or timestamp_container.get("words", []),
        ("word", "text", "token"),
    )
    segments = _parse_stamp_list(
        timestamp_container.get("segment", [])
        or timestamp_container.get("segments", []),
        ("segment", "text"),
    )

    if not segments and words:
        segments = [WordStamp(text=" ".join(word.text for word in words), start=words[0].start, end=words[-1].end)]

    return words, segments


class NemoParakeetBackend(BaseParakeetBackend):
    name = "nemo"

    def __init__(self, model_id: str | None = None) -> None:
        super().__init__(model_id or NEMO_MODEL_ID)

    def load(self) -> None:
        try:
            import nemo.collections.asr as nemo_asr
        except Exception as exc:  # pragma: no cover - optional heavy dependency
            raise BackendUnavailable(
                "Backend NeMo indisponible. Installe `requirements-nemo.txt`."
            ) from exc

        self.model = nemo_asr.models.ASRModel.from_pretrained(model_name=self.model_id)
        try:
            self.model.change_attention_model(
                self_attention_model="rel_pos_local_attn",
                att_context_size=[256, 256],
            )
        except Exception:
            # Older NeMo builds or converted models may not expose this. It is an
            # optimization for long media, not required for correctness.
            pass

    def transcribe(self, wav_path: str, chunk_duration: float, overlap_duration: float) -> TranscriptionResult:
        if self.model is None:
            self.load()

        output = self.model.transcribe([wav_path], timestamps=True)
        hypothesis = output[0]
        text = getattr(hypothesis, "text", str(hypothesis))
        words, segments = _parse_nemo_timestamps(hypothesis)
        return TranscriptionResult(text=text, words=words, segments=segments, backend=self.name, model_id=self.model_id)


class MlxParakeetBackend(BaseParakeetBackend):
    name = "mlx"

    def __init__(self, model_id: str | None = None) -> None:
        super().__init__(model_id or MLX_MODEL_ID)

    def load(self) -> None:
        try:
            from parakeet_mlx import from_pretrained
        except Exception as exc:  # pragma: no cover - optional platform dependency
            raise BackendUnavailable(
                "Backend MLX indisponible. Sur Apple Silicon, installe `requirements-mlx.txt`."
            ) from exc

        self.model = from_pretrained(self.model_id)

    def transcribe(self, wav_path: str, chunk_duration: float, overlap_duration: float) -> TranscriptionResult:
        if self.model is None:
            self.load()

        result = self.model.transcribe(
            wav_path,
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
        )
        text = getattr(result, "text", "")
        sentences = list(getattr(result, "sentences", []) or [])

        segments: list[WordStamp] = []
        words: list[WordStamp] = []
        for sentence in sentences:
            sentence_text = _text_attr(sentence, "text")
            start = _float_attr(sentence, "start")
            end = _float_attr(sentence, "end")
            if sentence_text and start is not None and end is not None:
                segments.append(WordStamp(sentence_text, start, end))

            tokens = list(getattr(sentence, "tokens", []) or [])
            words.extend(_parse_stamp_list(tokens, ("text", "word", "token")))

        return TranscriptionResult(text=text, words=words, segments=segments, backend=self.name, model_id=self.model_id)


class FasterWhisperBackend(BaseParakeetBackend):
    name = "whisper"

    def __init__(self, model_id: str | None = None) -> None:
        super().__init__(model_id or WHISPER_MODEL_ID)

    def load(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - optional dependency
            raise BackendUnavailable(
                "Backend Whisper indisponible. Installe `requirements-whisper.txt`."
            ) from exc

        device = "cpu"
        compute_types: list[str] = ["int8"]
        try:
            import torch

            if torch.cuda.is_available():
                device = "cuda"
                compute_types = ["float16", "int8_float16", "int8", "float32"]
        except Exception:
            pass

        errors: list[str] = []
        for compute_type in compute_types:
            try:
                self.model = WhisperModel(self.model_id, device=device, compute_type=compute_type)
                return
            except Exception as exc:
                errors.append(f"{compute_type}: {exc}")

        raise BackendUnavailable(
            "Impossible de charger faster-whisper sur cette machine. " + " | ".join(errors)
        )

    def transcribe(self, wav_path: str, chunk_duration: float, overlap_duration: float) -> TranscriptionResult:
        if self.model is None:
            self.load()

        segments_iter, info = self.model.transcribe(
            wav_path,
            language="fr",
            vad_filter=True,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        del info

        text_parts: list[str] = []
        words: list[WordStamp] = []
        segments: list[WordStamp] = []
        for segment in segments_iter:
            segment_text = str(getattr(segment, "text", "") or "").strip()
            start = _float_attr(segment, "start")
            end = _float_attr(segment, "end")
            if segment_text:
                text_parts.append(segment_text)
            if segment_text and start is not None and end is not None:
                segments.append(WordStamp(text=segment_text, start=start, end=end))

            for word in list(getattr(segment, "words", []) or []):
                word_text = _text_attr(word, "word", "text")
                word_start = _float_attr(word, "start")
                word_end = _float_attr(word, "end")
                if word_text and word_start is not None and word_end is not None:
                    words.append(WordStamp(text=word_text, start=word_start, end=word_end))

        return TranscriptionResult(
            text=" ".join(text_parts).strip(),
            words=words,
            segments=segments,
            backend=self.name,
            model_id=self.model_id,
        )


def unload_backend_instance(backend: BaseParakeetBackend | None) -> None:
    if backend is None:
        return
    try:
        backend.unload()
    finally:
        try:
            import gc

            gc.collect()
        except Exception:
            pass
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def create_backend(preferred: str = "auto", model_id: str | None = None) -> BaseParakeetBackend:
    preferred = (preferred or "auto").lower()
    errors: list[str] = []

    candidates: list[type[BaseParakeetBackend]]
    if preferred == "nemo":
        candidates = [NemoParakeetBackend]
    elif preferred == "whisper":
        candidates = [FasterWhisperBackend]
    elif preferred == "mlx":
        candidates = [MlxParakeetBackend]
    elif platform.system() == "Darwin":
        candidates = [MlxParakeetBackend, FasterWhisperBackend, NemoParakeetBackend]
    else:
        candidates = [NemoParakeetBackend, FasterWhisperBackend, MlxParakeetBackend]

    for backend_cls in candidates:
        backend = backend_cls(model_id=model_id)
        try:
            backend.load()
            return backend
        except BackendUnavailable as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"{backend_cls.name}: {exc}")

    raise BackendUnavailable("Aucun backend Parakeet disponible. " + " | ".join(errors))
