"""
CareLoop - Intent Recognition and Argument Extractor (intents.py)

Rule-based, case-insensitive conversational classifier that maps elder natural language
utterances to MCP tool names and argument payloads:
  - "took/had/taken/ate" + medicine -> ("log_medication", {"name": ...})
  - "feel/feeling/not well/dizzy/pain/fell/..." -> ("log_wellness", {"mood": ..., "symptoms": ...})
  - "did I take/what's left/missed/..." -> ("check_missed_meds", {})
  - "how was my day/summary" -> ("get_daily_summary", {})
  - fallback -> (None, {})

Note: intents.py does NOT call tools.py directly. The server connects as an MCP client
to /mcp and calls the tool via session.call_tool().
"""

from __future__ import annotations

import re
from typing import Any

# Regex triggers for conversational intents
MED_INTENT_PATTERN = re.compile(r"\b(took|had|taken|ate)\b", re.IGNORECASE)
WELLNESS_INTENT_PATTERN = re.compile(
    r"\b(feel|feeling|not well|dizzy|pain|fell|fall|falling|bleed|bleeding|breathe|breath)\b",
    re.IGNORECASE,
)
MISSED_INTENT_PATTERN = re.compile(
    r"\b(did i take|what'?s left|whats left|miss|missed|missing)\b",
    re.IGNORECASE,
)
SUMMARY_INTENT_PATTERN = re.compile(
    r"\b(how was my day|summary)\b",
    re.IGNORECASE,
)

# Standard fallback guidance
FALLBACK_REPLY = "I can log medicines, check how you feel, or give a summary."


def _extract_medicine_name(text: str) -> str:
    """Extract medication name candidate following intake action verbs."""
    match = re.search(
        r"\b(?:took|had|taken|ate)\s+(?:my\s+|the\s+|a\s+|some\s+)?([a-zA-Z0-9\s]+?)"
        r"(?:\s+(?:already|just now|today|at|this morning)|\.|\!|\?|$)",
        text,
        re.IGNORECASE,
    )
    if match:
        extracted = match.group(1).strip()
        if extracted:
            return extracted
    return text.strip()


def _extract_wellness_info(text: str) -> tuple[str, str]:
    """
    Extract mood and symptoms from elder statement.
    Returns (mood, symptoms).
    """
    lower = text.lower()

    if "fell" in lower or "fall" in lower:
        return "unsteady", ""
    if "dizzy" in lower or "dizziness" in lower:
        return "dizzy", ""
    if "chest pain" in lower:
        return "chest pain", ""
    if "pain" in lower:
        return "in pain", ""
    if "bleed" in lower or "bleeding" in lower:
        return "bleeding", ""
    if "breathe" in lower or "breath" in lower:
        return "short of breath", ""
    if "not well" in lower or "unwell" in lower:
        return "unwell", ""

    feel_match = re.search(r"\b(?:feel|feeling)\s+(.+?)(?:\.|\!|\?|$)", text, re.IGNORECASE)
    if feel_match:
        extracted_state = feel_match.group(1).strip().lower()
        return extracted_state, ""

    return "neutral", ""


def match_intent(text: str) -> dict[str, Any]:
    """
    Analyze elder text and return the matched MCP tool and arguments.

    Returns:
        dict with:
          - 'tool': tool name str or None
          - 'arguments': dict of keyword arguments to pass to session.call_tool()
          - 'fallback_reply': guidance string if no tool matched
    """
    if not text or not text.strip():
        return {
            "tool": None,
            "arguments": {},
            "fallback_reply": FALLBACK_REPLY,
        }

    clean_text = text.strip()
    lower = clean_text.lower()

    # 1. Medication Intake Intent
    if MED_INTENT_PATTERN.search(lower):
        med_candidate = _extract_medicine_name(clean_text)
        return {
            "tool": "log_medication",
            "arguments": {"name": med_candidate},
            "fallback_reply": None,
        }

    # 2. Wellness Check-in Intent
    if WELLNESS_INTENT_PATTERN.search(lower):
        mood, symptoms = _extract_wellness_info(clean_text)
        arguments: dict[str, Any] = {"mood": mood}
        if symptoms:
            arguments["symptoms"] = symptoms
        return {
            "tool": "log_wellness",
            "arguments": arguments,
            "fallback_reply": None,
        }

    # 3. Missed Meds Intent
    if MISSED_INTENT_PATTERN.search(lower):
        return {
            "tool": "check_missed_meds",
            "arguments": {},
            "fallback_reply": None,
        }

    # 4. Daily Summary Intent
    if SUMMARY_INTENT_PATTERN.search(lower):
        return {
            "tool": "get_daily_summary",
            "arguments": {},
            "fallback_reply": None,
        }

    # 5. Fallback
    return {
        "tool": None,
        "arguments": {},
        "fallback_reply": FALLBACK_REPLY,
    }


def format_reply(
    tool: str | None,
    arguments: dict[str, Any],
    result_data: dict[str, Any] | None,
) -> str:
    """
    Construct a friendly spoken response for the elder based on the MCP tool result.
    """
    if not tool or not result_data:
        return FALLBACK_REPLY

    if tool == "log_medication":
        if result_data.get("message"):
            return result_data["message"]

        name = result_data.get("name", arguments.get("name", "medication"))
        matched = result_data.get("matched_scheduled_name")
        sched_time = result_data.get("scheduled_time")
        is_late = result_data.get("is_late", False)
        is_on_time = result_data.get("is_on_time", False)
        is_early = result_data.get("is_early", False)

        if is_late:
            time_display = sched_time or "the scheduled time"
            return f"I've logged your {name}. It was due at {time_display}, so I've noted it as late."
        elif is_on_time:
            return f"I've logged your {name}. Great job taking your medication on time!"
        elif is_early:
            time_display = sched_time or "the scheduled time"
            return f"I've logged your {name}. It was due at {time_display}, so I've noted it as taken early."
        elif matched:
            return f"I've logged your {name}."
        return f"I've logged that you took {name}."

    if tool == "log_wellness":
        if result_data.get("alert_triggered"):
            return (
                "I've noted that you're not feeling well and alerted your family "
                "right away to check in. Please stay seated and rest."
            )
        mood = arguments.get("mood", "recorded")
        return f"Thank you for checking in. I've noted that you're feeling {mood.lower()}."

    if tool == "check_missed_meds":
        missed = result_data.get("missed", [])
        upcoming = result_data.get("upcoming", [])
        if missed:
            missed_names = ", ".join(m["name"] for m in missed)
            return f"You haven't logged your {missed_names} yet today. Please take it when you can!"
        if upcoming:
            upcoming_names = ", ".join(f"{u['name']} at {u['scheduled_time']}" for u in upcoming)
            return f"You are up to date! Still scheduled for later: {upcoming_names}."
        return "You're all caught up! All scheduled medications for today have been taken."

    if tool == "get_daily_summary":
        taken_cnt = len(result_data.get("taken", []))
        missed_cnt = len(result_data.get("missed", []))
        alerts_cnt = len(result_data.get("alerts", []))
        if alerts_cnt > 0:
            return (
                f"Today you took {taken_cnt} medications with {missed_cnt} missed. "
                f"There were {alerts_cnt} safety alerts flagged."
            )
        return (
            f"You've had a solid day: {taken_cnt} medications taken, "
            f"{missed_cnt} missed, and zero safety alerts."
        )

    return f"Completed {tool}."


# Backwards compatibility helper
def handle_message(text: str) -> dict[str, Any]:
    """Helper that routes via match_intent for testing or non-MCP fallback."""
    intent = match_intent(text)
    if not intent["tool"]:
        return {"reply": intent["fallback_reply"], "tool": None, "arguments": {}}
    return {
        "reply": f"Routed to {intent['tool']}",
        "tool": intent["tool"],
        "arguments": intent["arguments"],
    }
