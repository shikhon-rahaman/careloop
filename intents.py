"""
CareLoop - Intent Recognition and Conversational Handler (intents.py)

Rule-based, case-insensitive conversational interface that maps elder natural language
utterances to underlying CareLoop tools:
  - "took/had/taken/ate" + medicine -> log_medication
  - "feel/feeling/not well/dizzy/pain/fell/fall/bleeding/breathe" -> log_wellness
  - "did I take/what's left/missed/miss/missing" -> check_missed_meds
  - "how was my day/summary" -> get_daily_summary
  - fallback -> friendly guidance prompt
"""

from __future__ import annotations

import re
from typing import Any

from tools import (
    check_missed_meds,
    get_daily_summary,
    log_medication,
    log_wellness,
)

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

# Standard fallback response
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
        return "Unsteady", text.strip()
    if "dizzy" in lower:
        return "Dizzy", text.strip()
    if "chest pain" in lower or "pain" in lower:
        return "In pain", text.strip()
    if "not well" in lower:
        return "Unwell", text.strip()
    if "bleed" in lower or "bleeding" in lower:
        return "Bleeding", text.strip()
    if "breathe" in lower or "breath" in lower:
        return "Short of breath", text.strip()

    # Generic feel / feeling extraction
    feel_match = re.search(r"\b(?:feel|feeling)\s+(.+?)(?:\.|\!|\?|$)", text, re.IGNORECASE)
    if feel_match:
        extracted_state = feel_match.group(1).strip()
        return extracted_state.capitalize(), text.strip()

    return "Neutral", text.strip()


def handle_message(text: str) -> dict[str, Any]:
    """
    Process an elder conversational message and route to the corresponding tool.

    Parameters:
        text: Input message from the elder or caregiver.

    Returns:
        dict with keys:
          - 'reply': Friendly, spoken-style, short response.
          - 'tool': Name of tool called (or None for fallback).
          - 'data': (optional) underlying tool response object.
    """
    if not text or not text.strip():
        return {
            "reply": FALLBACK_REPLY,
            "tool": None,
        }

    clean_text = text.strip()
    lower = clean_text.lower()

    # 1. Medication Intake Intent ("took/had/taken/ate" + medicine)
    if MED_INTENT_PATTERN.search(lower):
        med_candidate = _extract_medicine_name(clean_text)
        try:
            result = log_medication(name=med_candidate)
            name = result["name"]
            scheduled = result.get("matched_scheduled_name")
            if scheduled:
                reply = f"I've logged your {name}. Great job taking your medication on time!"
            else:
                reply = f"I've logged that you took {name}."
            return {
                "reply": reply,
                "tool": "log_medication",
                "data": result,
            }
        except Exception as exc:
            return {
                "reply": f"I couldn't record that medication: {exc}",
                "tool": "log_medication",
            }

    # 2. Wellness Check-in Intent ("feel/feeling/not well/dizzy/pain/fell/fall/bleeding/breathe")
    if WELLNESS_INTENT_PATTERN.search(lower):
        mood, symptoms = _extract_wellness_info(clean_text)
        try:
            result = log_wellness(mood=mood, symptoms=symptoms)
            if result.get("alert_triggered"):
                reply = (
                    "I've noted that you're not feeling well and alerted your family "
                    "right away to check in. Please stay seated and rest."
                )
            else:
                reply = f"Thank you for checking in. I've noted that you're feeling {mood.lower()}."
            return {
                "reply": reply,
                "tool": "log_wellness",
                "data": result,
            }
        except Exception as exc:
            return {
                "reply": f"I couldn't log your wellness check: {exc}",
                "tool": "log_wellness",
            }

    # 3. Missed Meds Check Intent ("did I take/what's left/missed/miss/missing")
    if MISSED_INTENT_PATTERN.search(lower):
        try:
            result = check_missed_meds()
            missed = result.get("missed", [])
            upcoming = result.get("upcoming", [])
            if missed:
                missed_names = ", ".join(m["name"] for m in missed)
                reply = f"You haven't logged your {missed_names} yet today. Please take it when you can!"
            elif upcoming:
                upcoming_names = ", ".join(f"{u['name']} at {u['scheduled_time']}" for u in upcoming)
                reply = f"You are up to date! Still scheduled for later: {upcoming_names}."
            else:
                reply = "You're all caught up! All scheduled medications for today have been taken."
            return {
                "reply": reply,
                "tool": "check_missed_meds",
                "data": result,
            }
        except Exception as exc:
            return {
                "reply": f"I couldn't check your schedule: {exc}",
                "tool": "check_missed_meds",
            }

    # 4. Daily Summary Intent ("how was my day/summary")
    if SUMMARY_INTENT_PATTERN.search(lower):
        try:
            result = get_daily_summary()
            taken_cnt = len(result.get("taken", []))
            missed_cnt = len(result.get("missed", []))
            alerts_cnt = len(result.get("alerts", []))
            if alerts_cnt > 0:
                reply = (
                    f"Today you took {taken_cnt} medications with {missed_cnt} missed. "
                    f"There were {alerts_cnt} safety alerts flagged."
                )
            else:
                reply = (
                    f"You've had a solid day: {taken_cnt} medications taken, "
                    f"{missed_cnt} missed, and zero safety alerts."
                )
            return {
                "reply": reply,
                "tool": "get_daily_summary",
                "data": result,
            }
        except Exception as exc:
            return {
                "reply": f"I couldn't generate your summary: {exc}",
                "tool": "get_daily_summary",
            }

    # 5. Fallback Response
    return {
        "reply": FALLBACK_REPLY,
        "tool": None,
    }
