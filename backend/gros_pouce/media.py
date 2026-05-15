from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise MediaError(
            "ffmpeg introuvable. Installe ffmpeg et verifie qu'il est disponible dans le PATH."
        )
    return ffmpeg


def extract_audio_to_wav(
    media_path: str,
    output_path: str,
    trim_start_seconds: float | None = None,
    trim_end_seconds: float | None = None,
) -> str:
    source = Path(media_path)
    if not source.exists():
        raise MediaError(f"Fichier media introuvable: {media_path}")

    ffmpeg = ensure_ffmpeg()
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]

    start = None
    end = None
    if trim_start_seconds is not None:
        start = max(0.0, float(trim_start_seconds))
        if start > 0:
            cmd.extend(["-ss", f"{start:.3f}"])
    if trim_end_seconds is not None:
        end = max(0.0, float(trim_end_seconds))

    cmd.extend(["-i", str(source)])

    if start is not None and end is not None and end > start:
        cmd.extend(["-t", f"{end - start:.3f}"])

    cmd.extend(["-vn", "-ac", "1", "-ar", "16000", "-f", "wav", output_path])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "erreur ffmpeg inconnue"
        raise MediaError(f"Extraction audio echouee: {detail}")
    return output_path
