from __future__ import annotations

from dataclasses import dataclass
from textwrap import wrap
from typing import Iterable, Sequence


@dataclass(frozen=True)
class WordStamp:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SourceCue:
    clip_key: str
    source_label: str
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleOptions:
    max_line_chars: int = 42
    max_lines: int = 2
    min_duration: float = 0.75
    max_duration: float = 5.5
    max_cps: float = 18.0
    pause_break: float = 0.45

    @property
    def max_chars(self) -> int:
        return max(12, self.max_line_chars * self.max_lines)


PUNCT_NO_SPACE_BEFORE = set(".,;:!?)]}%")
PUNCT_NO_SPACE_AFTER = set("([{%")
SENTENCE_ENDINGS = set(".!?")


def normalize_word(text: str) -> str:
    return (text or "").strip()


def join_words(words: Sequence[WordStamp]) -> str:
    output = ""
    for stamp in words:
        word = normalize_word(stamp.text)
        if not word:
            continue

        if not output:
            output = word
            continue

        if word[0] in PUNCT_NO_SPACE_BEFORE:
            output += word
        elif output[-1] in PUNCT_NO_SPACE_AFTER or output.endswith("'") or output.endswith("’"):
            output += word
        else:
            output += " " + word
    return " ".join(output.split())


def wrap_subtitle_text(text: str, max_line_chars: int, max_lines: int) -> str:
    lines = wrap(
        text,
        width=max(12, max_line_chars),
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not lines:
        return text
    if len(lines) <= max_lines:
        return "\n".join(lines)

    kept = lines[: max_lines - 1]
    kept.append(" ".join(lines[max_lines - 1 :]))
    return "\n".join(kept)


def _should_break_before(
    current: Sequence[WordStamp],
    next_word: WordStamp,
    options: SubtitleOptions,
) -> bool:
    if not current:
        return False

    gap = max(0.0, next_word.start - current[-1].end)
    if gap >= options.pause_break:
        return True

    candidate = list(current) + [next_word]
    text = join_words(candidate)
    duration = max(0.01, candidate[-1].end - candidate[0].start)
    cps = len(text) / duration

    if len(text) > options.max_chars:
        return True
    if duration > options.max_duration:
        return True
    if len(current) >= 4 and cps > options.max_cps and duration >= options.min_duration:
        return True
    return False


def _should_flush_after(current: Sequence[WordStamp], options: SubtitleOptions) -> bool:
    if not current:
        return False

    text = join_words(current)
    duration = max(0.01, current[-1].end - current[0].start)
    if duration < options.min_duration:
        return False

    if text and text[-1] in SENTENCE_ENDINGS and len(text) >= min(28, options.max_line_chars):
        return True
    return False


def build_cues_from_words(
    words: Iterable[WordStamp],
    options: SubtitleOptions | None = None,
) -> list[SubtitleCue]:
    opts = options or SubtitleOptions()
    cleaned = [
        word
        for word in words
        if normalize_word(word.text) and word.end > word.start and word.start >= 0
    ]

    cues: list[SubtitleCue] = []
    current: list[WordStamp] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        text = wrap_subtitle_text(join_words(current), opts.max_line_chars, opts.max_lines)
        start = current[0].start
        end = max(current[-1].end, start + opts.min_duration)
        if cues and start < cues[-1].end:
            start = cues[-1].end + 0.01
            end = max(end, start + opts.min_duration)
        cues.append(SubtitleCue(len(cues) + 1, start, end, text))
        current = []

    for word in cleaned:
        if _should_break_before(current, word, opts):
            flush()
        current.append(word)
        if _should_flush_after(current, opts):
            flush()

    flush()
    return cues


def build_cues_from_segments(
    segments: Iterable[WordStamp],
    options: SubtitleOptions | None = None,
) -> list[SubtitleCue]:
    opts = options or SubtitleOptions()
    cues: list[SubtitleCue] = []
    for segment in segments:
        text = " ".join(normalize_word(segment.text).split())
        if not text or segment.end <= segment.start:
            continue
        wrapped = wrap_subtitle_text(text, opts.max_line_chars, opts.max_lines)
        cues.append(
            SubtitleCue(
                index=len(cues) + 1,
                start=max(0.0, segment.start),
                end=max(segment.end, segment.start + opts.min_duration),
                text=wrapped,
            )
        )
    return cues


def seconds_to_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000.0))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def cues_to_srt(cues: Sequence[SubtitleCue]) -> str:
    blocks = []
    for cue in cues:
        blocks.append(
            "\n".join(
                [
                    str(cue.index),
                    f"{seconds_to_srt_time(cue.start)} --> {seconds_to_srt_time(cue.end)}",
                    cue.text,
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def shift_cues(
    cues: Iterable[SubtitleCue],
    offset_seconds: float,
    clip_key: str,
    source_label: str,
) -> list[SourceCue]:
    shifted: list[SourceCue] = []
    offset = float(offset_seconds or 0.0)
    for cue in cues:
        shifted.append(
            SourceCue(
                clip_key=clip_key,
                source_label=source_label,
                start=max(0.0, cue.start + offset),
                end=max(0.0, cue.end + offset),
                text=cue.text,
            )
        )
    return shifted


def source_cues_to_subtitle_cues(cues: Iterable[SourceCue]) -> list[SubtitleCue]:
    ordered = sorted(cues, key=lambda cue: (cue.start, cue.end, cue.source_label, cue.clip_key))
    subtitles: list[SubtitleCue] = []
    for cue in ordered:
        subtitles.append(
            SubtitleCue(
                index=len(subtitles) + 1,
                start=cue.start,
                end=cue.end,
                text=cue.text,
            )
        )
    return subtitles
