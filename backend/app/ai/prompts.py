"""Prompts and JSON schemas shared by every AI provider."""
from __future__ import annotations

import json

CATEGORIES = [
    "Funny",
    "Fail",
    "Clutch / Highlight",
    "Rage",
    "Jumpscare / Scary",
    "Wholesome",
    "Chat / Donation Reaction",
    "IRL",
    "Music",
    "Collab / Guest",
    "Drama / Serious",
    "Other",
]


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


ANALYSIS_SCHEMA = _obj({
    "summary": {"type": "string", "description": "2-3 sentences: what actually happens, in order."},
    "category": {"type": "string", "enum": CATEGORIES},
    "secondary_categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
    "tags": {"type": "array", "items": {"type": "string"}},
    "moments": {"type": "array", "items": _obj({
        "t": {"type": "number", "description": "seconds from clip start"},
        "description": {"type": "string"},
    })},
    "on_screen": {"type": "array", "items": {"type": "string"}},
    "mood": {"type": "string"},
    "energy": {"type": "integer", "description": "1 (calm) to 5 (screaming)"},
    "profanity": {"type": "boolean"},
    "best_in": {"type": "number"},
    "best_out": {"type": "number"},
    "search_phrases": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "number", "description": "0-1, how sure you are of the summary"},
})

VERIFY_SCHEMA = _obj({
    "checks": {"type": "array", "items": _obj({
        "claim": {"type": "string"},
        "question": {"type": "string"},
        "answer": {"type": "string"},
        "supported": {"type": "string", "enum": ["yes", "no", "unsure"]},
    })},
    "remove_tags": {"type": "array", "items": {"type": "string"}},
    "corrected_summary": {"type": "string", "description": "Empty if the summary is fine."},
    "confidence": {"type": "number"},
})

DESCRIBE_SYSTEM = """You catalogue Twitch clips for a video editor who needs to find moments fast.
You get the clip's metadata, a timestamped speech transcript and still frames taken at known times.
Describe only what the evidence shows. If the frames and transcript don't make something clear, say so
and lower your confidence rather than guessing. Tags should be concrete and searchable (game events,
actions, reactions, objects, people, memes). search_phrases are short things an editor might type to
find this clip. best_in/best_out are the seconds you'd cut the clip to for a tight edit."""

VERIFY_SYSTEM = """You fact-check a description of a Twitch clip against the evidence (frames + transcript).
Pick the 2-4 most important or most doubtful claims in the description, turn each into a yes/no question,
and answer it strictly from the evidence. 'unsure' is a fine answer when the evidence doesn't show it.
List any tags that are not supported so they can be removed. Only write corrected_summary if the
summary contains a claim the evidence contradicts."""

ASK_SYSTEM = """You answer a video editor's questions about one Twitch clip, using only the frames,
transcript and previous analysis given. Cite timestamps like (0:12) when you can. If the evidence
doesn't answer the question, say that plainly. Keep answers short."""


def clip_context(clip: dict, frames: list[dict], transcript: str) -> str:
    meta = {
        "title": clip.get("title"),
        "streamer": clip.get("streamer_name"),
        "game": clip.get("game_name"),
        "duration_seconds": clip.get("duration"),
        "clipped_by": clip.get("creator_name"),
        "views": clip.get("view_count"),
        "created_at": clip.get("created_at"),
    }
    times = ", ".join(f"image {i + 1} = {f['t']:.1f}s" for i, f in enumerate(frames))
    return (
        f"Clip metadata:\n{json.dumps(meta, indent=1)}\n\n"
        f"Frames (in order): {times or 'none'}\n\n"
        f"Transcript:\n{transcript}"
    )


def describe_prompt(context: str) -> str:
    return f"{context}\n\nDescribe this clip. Pick one main category from the list."


def verify_prompt(context: str, analysis: dict) -> str:
    return (
        f"{context}\n\nDescription to check:\n{json.dumps(analysis, indent=1)}\n\n"
        "Fact-check it."
    )


def ask_prompt(context: str, analysis: dict | None, history: list[dict], question: str) -> str:
    prior = "\n".join(f"Q: {h['question']}\nA: {h['answer']}" for h in history[-6:])
    return (
        f"{context}\n\nPrevious analysis:\n{json.dumps(analysis or {}, indent=1)}\n\n"
        + (f"Earlier questions:\n{prior}\n\n" if prior else "")
        + f"Question: {question}"
    )
