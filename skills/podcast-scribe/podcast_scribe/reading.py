"""Reading turns derived from ASR slices, without changing source records.

Slice boundaries are alignment details, not speaker changes or paragraphs.
Keep indices so chapters can still address the original passage within a turn.
"""

from __future__ import annotations


def segment_text_parts(segments: list[dict]) -> list[str]:
    """Return joinable text fragments, retaining explicit paragraph breaks.

Chinese slices join directly; English words/sentences need a separating space.
Do not infer punctuation, remove repetitions, or rewrite editorial text here.
"""
    parts = []
    previous = ""
    for segment in segments:
        value = str(segment.get("text") or segment.get("raw_text") or "").strip()
        prefix = ""
        if previous and value:
            left, right = previous[-1], value[0]
            if left.isascii() and right.isascii() and right.isalnum() and (
                left.isalnum() or left in ".,!?;:)\"'"
            ):
                prefix = " "
        parts.append(prefix + value)
        if value:
            previous = value
    return parts


def reading_turns(episode: dict) -> list[dict]:
    """Group adjacent slices with the same known speaker into reading turns.

An unknown identity is never evidence that two slices share a speaker. Chapter
boundaries retain inline anchors, and never split an uninterrupted sentence.
"""
    known = {s.get("id") for s in episode.get("speakers", []) if s.get("id")}
    turns = []
    for index, segment in enumerate(episode.get("segments", [])):
        speaker = segment.get("speaker_id")
        if not turns or speaker not in known or turns[-1]["speaker_id"] != speaker:
            turns.append({
                "speaker_id": speaker,
                "start": segment.get("start", 0),
                "end": segment.get("end", 0),
                "segments": [],
                "needs_review": False,
            })
        turn = turns[-1]
        turn["segments"].append({**segment, "index": index})
        turn["end"] = max(turn["end"], segment.get("end", 0))
        turn["needs_review"] |= segment.get("review_status") != "reviewed"
    for turn in turns:
        turn["text"] = "".join(segment_text_parts(turn["segments"]))
    return turns
