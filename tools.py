"""
CareLoop - Tools Module (tools.py)

Core application tools for elder care monitoring:
1. log_medication: Fuzzy-matches medication names to schedule and logs intake.
2. log_wellness: Records mood and symptoms; triggers high-severity family alerts on critical symptoms.
3. get_daily_summary: Aggregates taken meds, missed meds, moods, and alerts for today.
4. check_missed_meds: Identifies scheduled meds whose time has passed without an intake log today.
5. alert_family: Dispatches urgent alerts (low/medium/high), persists to DB, and prints to console.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from db import (
    DEFAULT_DB_PATH,
    get_alerts,
    get_med_logs,
    get_schedule,
    get_wellness_logs,
    init_db,
    insert_alert,
    insert_med_log,
    insert_wellness_log,
    reset_db,
)

# Common synonym mappings for dosage forms and medical terminology
SYNONYM_MAP: dict[str, str] = {
    "pill": "tablet",
    "pills": "tablet",
    "tab": "tablet",
    "tabs": "tablet",
    "cap": "tablet",
    "caps": "tablet",
    "capsule": "tablet",
    "capsules": "tablet",
    "vit": "vitamin",
    "vits": "vitamin",
    "blood pressure": "bp",
    "blood-pressure": "bp",
}

# Critical symptoms that trigger an immediate high-severity family alert
CRITICAL_SYMPTOM_PATTERNS: list[str] = [
    r"\bdizzy\b",
    r"\bdizziness\b",
    r"\bfell\b",
    r"\bfall\b",
    r"\bfalling\b",
    r"\bchest\s+pain\b",
    r"\bcan'?t\s+breathe\b",
    r"\bcannot\s+breathe\b",
    r"\bbleeding\b",
    r"\bbleed\b",
]


def _normalize_name(name: str) -> str:
    """Normalize medication name by lowercasing and standardizing synonyms."""
    text = name.strip().lower()
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    normalized_tokens = [SYNONYM_MAP.get(token, token) for token in tokens]
    return " ".join(normalized_tokens)


def _fuzzy_match_schedule(
    input_name: str,
    schedule: list[dict[str, Any]],
    threshold: float = 0.50,
) -> tuple[dict[str, Any] | None, float]:
    """
    Fuzzy-match an input medication name against the scheduled medications.

    Uses a combination of exact matching, synonym normalization,
    token overlap, and SequenceMatcher similarity.

    Returns:
        tuple[dict | None, float]: Matched schedule entry (or None) and confidence score (0.0 to 1.0).
    """
    if not schedule or not input_name.strip():
        return None, 0.0

    norm_input = _normalize_name(input_name)
    input_tokens = set(norm_input.split())

    best_entry: dict[str, Any] | None = None
    best_score: float = 0.0

    for entry in schedule:
        sched_name = entry["name"]
        norm_sched = _normalize_name(sched_name)
        sched_tokens = set(norm_sched.split())

        # Exact normalized match
        if norm_input == norm_sched:
            return entry, 1.0

        # Substring / token inclusion
        if norm_sched in norm_input or norm_input in norm_sched:
            score_sub = 0.85
        else:
            score_sub = 0.0

        # Token overlap (Jaccard similarity)
        intersection = input_tokens & sched_tokens
        union = input_tokens | sched_tokens
        jaccard = len(intersection) / len(union) if union else 0.0

        # Sequence matcher ratio
        seq_ratio = difflib.SequenceMatcher(None, norm_input, norm_sched).ratio()

        # Weighted aggregate score
        score = max(score_sub, jaccard, seq_ratio)

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_score >= threshold:
        return best_entry, best_score
    return None, best_score


# ---------------------------------------------------------------------------
# Core CareLoop Tools
# ---------------------------------------------------------------------------

def _send_telegram_notification(reason: str, severity: str, timestamp: str) -> bool:
    """Send an emergency alert to Telegram if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        return False

    text = (
        f"🚨 *CareLoop Alert*\n"
        f"*Severity:* {severity.upper()}\n"
        f"*Time:* {timestamp}\n"
        f"*Reason:* {reason}"
    )
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception as exc:
        print(f"[Telegram Alert Dispatch Info] Notification skipped/failed: {exc}")
        return False


def alert_family(
    reason: str,
    severity: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Dispatch an urgent alert to family members, record in SQLite, and print to console.

    Parameters:
        reason: Description of the alert cause or triggered incident.
        severity: Alert severity level; must be 'low', 'medium', or 'high'.
        db_path: Optional custom SQLite database file path.

    Returns:
        dict: Persisted alert details including id, reason, severity, timestamp, and dispatch status.

    Raises:
        ValueError: If severity is not 'low', 'medium', or 'high'.
    """
    normalized_severity = severity.strip().lower()
    valid_severities = {"low", "medium", "high"}
    if normalized_severity not in valid_severities:
        raise ValueError(
            f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(valid_severities))}"
        )

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alert_id = insert_alert(
        reason=reason,
        severity=normalized_severity,
        created_at=created_at,
        db_path=db_path,
    )

    # Optional Telegram notification if credentials provided via environment
    telegram_sent = _send_telegram_notification(
        reason=reason.strip(),
        severity=normalized_severity,
        timestamp=created_at,
    )

    # Visual console alert notification
    banner_char = "!" if normalized_severity == "high" else ("*" if normalized_severity == "medium" else "-")
    separator = banner_char * 60
    print(f"\n{separator}")
    print(f"[FAMILY ALERT - {normalized_severity.upper()}] {created_at}")
    print(f"Reason: {reason.strip()}")
    if telegram_sent:
        print("Dispatch: Sent to family Telegram channel")
    print(f"{separator}\n")

    return {
        "id": alert_id,
        "reason": reason.strip(),
        "severity": normalized_severity,
        "created_at": created_at,
        "status": "dispatched",
        "telegram_sent": telegram_sent,
    }


def log_medication(
    name: str,
    taken_at: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Log medication intake with fuzzy matching against scheduled medicines.

    For example, "bp pill" fuzzy-matches to scheduled "BP tablet".

    Parameters:
        name: Name of medication taken (e.g. 'bp pill', 'metformin', 'vit d').
        taken_at: Optional timestamp string ('YYYY-MM-DD HH:MM:SS'). Defaults to current time.
        db_path: Optional custom SQLite database file path.

    Returns:
        dict: Status, logged record id, matched scheduled name, confidence, and timestamp.
    """
    if not name or not name.strip():
        raise ValueError("Medication name cannot be empty.")

    if taken_at is None:
        taken_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    schedule = get_schedule(db_path=db_path)
    matched_entry, score = _fuzzy_match_schedule(name, schedule)

    # Record under canonical scheduled name if matched, else record raw name
    canonical_name = matched_entry["name"] if matched_entry else name.strip()

    log_id = insert_med_log(
        name=canonical_name,
        taken_at=taken_at,
        db_path=db_path,
    )

    return {
        "status": "success",
        "id": log_id,
        "name": canonical_name,
        "raw_input_name": name.strip(),
        "matched_scheduled_name": matched_entry["name"] if matched_entry else None,
        "scheduled_time": matched_entry["time_hhmm"] if matched_entry else None,
        "match_confidence": round(score, 2),
        "taken_at": taken_at,
        "message": (
            f"Successfully logged '{canonical_name}'"
            + (f" (matched from '{name.strip()}', confidence: {score:.0%})" if matched_entry else "")
        ),
    }


def log_wellness(
    mood: str,
    symptoms: str = "",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Record elder wellness check-in (mood and symptoms).

    If symptoms contain dizzy, fell, fall, chest pain, can't breathe, or bleeding,
    an automatic high-severity alert is dispatched to family.

    Parameters:
        mood: Subjective mood rating/description (e.g., 'Good', 'Tired', 'Anxious').
        symptoms: Description of any symptoms experienced (e.g., 'dizzy and fell').
        db_path: Optional custom SQLite database file path.

    Returns:
        dict: Log status, record id, mood, symptoms, alert_triggered flag, and alert details.
    """
    if not mood or not mood.strip():
        raise ValueError("Mood cannot be empty.")

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_symptoms = symptoms.strip()

    log_id = insert_wellness_log(
        mood=mood.strip(),
        symptoms=clean_symptoms,
        created_at=created_at,
        db_path=db_path,
    )

    # Check for critical symptom triggers
    triggered_symptoms: list[str] = []
    lower_symptoms = clean_symptoms.lower()
    for pattern in CRITICAL_SYMPTOM_PATTERNS:
        match = re.search(pattern, lower_symptoms)
        if match:
            triggered_symptoms.append(match.group(0))

    alert_result: dict[str, Any] | None = None
    if triggered_symptoms:
        reason = (
            f"High-risk symptom detected ({', '.join(set(triggered_symptoms))}) "
            f"during wellness check. Reported symptoms: '{clean_symptoms}'"
        )
        alert_result = alert_family(
            reason=reason,
            severity="high",
            db_path=db_path,
        )

    return {
        "status": "success",
        "id": log_id,
        "mood": mood.strip(),
        "symptoms": clean_symptoms,
        "created_at": created_at,
        "alert_triggered": bool(alert_result),
        "alert": alert_result,
    }


def check_missed_meds(
    current_time_hhmm: str | None = None,
    date_str: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Identify scheduled medications whose scheduled time has passed without an intake log today.

    Parameters:
        current_time_hhmm: Optional reference time 'HH:MM' (defaults to current clock time).
        date_str: Optional reference date 'YYYY-MM-DD' (defaults to today).
        db_path: Optional custom SQLite database file path.

    Returns:
        dict: Detailed breakdown containing missed meds, taken meds, and upcoming meds for today.
    """
    now = datetime.now()
    ref_date = date_str if date_str else now.strftime("%Y-%m-%d")
    ref_time = current_time_hhmm if current_time_hhmm else now.strftime("%H:%M")

    schedule = get_schedule(db_path=db_path)
    today_logs = get_med_logs(date_str=ref_date, db_path=db_path)

    # Standardize names of medications logged today
    taken_med_names = {_normalize_name(log["name"]) for log in today_logs}

    missed: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []

    for item in schedule:
        sched_name = item["name"]
        sched_time = item["time_hhmm"]
        norm_sched_name = _normalize_name(sched_name)

        # Check if medicine was taken today
        is_taken = norm_sched_name in taken_med_names or any(
            norm_sched_name in t or t in norm_sched_name for t in taken_med_names
        )

        entry_info = {
            "name": sched_name,
            "scheduled_time": sched_time,
        }

        if is_taken:
            taken.append(entry_info)
        elif sched_time <= ref_time:
            # Scheduled time has passed and no log exists
            missed.append({
                **entry_info,
                "missed_by_hhmm": ref_time,
            })
        else:
            upcoming.append(entry_info)

    return {
        "date": ref_date,
        "current_time": ref_time,
        "total_scheduled": len(schedule),
        "missed_count": len(missed),
        "missed": missed,
        "taken": taken,
        "upcoming": upcoming,
    }


def get_daily_summary(
    date_str: str | None = None,
    current_time_hhmm: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Generate daily summary: taken meds, missed meds, moods, and alerts for today.

    Parameters:
        date_str: Target date 'YYYY-MM-DD' (defaults to today).
        current_time_hhmm: Optional reference time 'HH:MM' (defaults to current clock time).
        db_path: Optional custom SQLite database file path.

    Returns:
        dict: Summary containing 'taken', 'missed', 'mood', and 'alerts' for the specified day.
    """
    ref_date = date_str if date_str else datetime.now().strftime("%Y-%m-%d")

    # 1. Med intake logs for today
    today_med_logs = get_med_logs(date_str=ref_date, db_path=db_path)

    # 2. Missed meds check
    missed_check = check_missed_meds(
        current_time_hhmm=current_time_hhmm,
        date_str=ref_date,
        db_path=db_path,
    )

    # 3. Wellness logs for today
    today_wellness = get_wellness_logs(date_str=ref_date, db_path=db_path)
    mood_list = [w["mood"] for w in today_wellness]

    # 4. Alerts for today
    today_alerts = get_alerts(date_str=ref_date, db_path=db_path)

    return {
        "date": ref_date,
        "taken": today_med_logs,
        "missed": missed_check["missed"],
        "mood": mood_list,
        "wellness_logs": today_wellness,
        "alerts": today_alerts,
        "summary": {
            "taken_count": len(today_med_logs),
            "missed_count": len(missed_check["missed"]),
            "wellness_check_count": len(today_wellness),
            "alerts_count": len(today_alerts),
        },
    }


# ---------------------------------------------------------------------------
# Main Demonstration / Self-Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 70)
    print(" CareLoop System - Verification & Demonstration")
    print("=" * 70)

    # 0. Initialize & Reset Database
    print("\n[Step 0] Initializing database and resetting logs...")
    init_db()
    reset_db()
    print("Schedule seeded:")
    for s in get_schedule():
        print(f"  - ID {s['id']}: {s['name']} at {s['time_hhmm']}")

    # 1. Test log_medication (fuzzy-matching "bp pill" -> "BP tablet")
    print("\n" + "-" * 70)
    print("[Exercise 1] log_medication(): Fuzzy match 'bp pill' and 'metformin'")
    print("-" * 70)
    med_res1 = log_medication(name="bp pill")
    print(f"Result 1 ('bp pill'): {json.dumps(med_res1, indent=2)}")

    med_res2 = log_medication(name="metformin")
    print(f"\nResult 2 ('metformin'): {json.dumps(med_res2, indent=2)}")

    # 2. Test log_wellness (normal vs critical symptoms)
    print("\n" + "-" * 70)
    print("[Exercise 2] log_wellness(): Normal check-in & Critical symptom detection")
    print("-" * 70)
    well_res1 = log_wellness(mood="Calm & cheerful", symptoms="Rested well, good appetite")
    print(f"Normal Wellness Check: {json.dumps(well_res1, indent=2)}")

    print("\nSubmitting critical wellness check (dizzy & fell)...")
    well_res2 = log_wellness(
        mood="Frightened & weak",
        symptoms="Fell down near the bedroom door and feeling very dizzy",
    )
    print(f"Critical Wellness Check: {json.dumps(well_res2, indent=2)}")

    # 3. Test check_missed_meds()
    print("\n" + "-" * 70)
    print("[Exercise 3] check_missed_meds(): Checking schedule vs logs for today")
    print("-" * 70)
    # Notice: BP tablet and Metformin were logged above.
    # Vitamin D (scheduled for 14:00) has not been logged.
    missed_res = check_missed_meds()
    print(f"Missed meds evaluation:\n{json.dumps(missed_res, indent=2)}")

    # 4. Test get_daily_summary()
    print("\n" + "-" * 70)
    print("[Exercise 4] get_daily_summary(): Aggregated summary for today")
    print("-" * 70)
    summary_res = get_daily_summary()
    print(f"Daily Summary for {summary_res['date']}:")
    print(f"  - Taken ({len(summary_res['taken'])}): {[m['name'] for m in summary_res['taken']]}")
    print(f"  - Missed ({len(summary_res['missed'])}): {[m['name'] for m in summary_res['missed']]}")
    print(f"  - Moods recorded ({len(summary_res['mood'])}): {summary_res['mood']}")
    print(f"  - Alerts recorded ({len(summary_res['alerts'])}): {[a['reason'] for a in summary_res['alerts']]}")
    print(f"\nFull summary JSON:\n{json.dumps(summary_res, indent=2)}")

    # 5. Test alert_family() directly
    print("\n" + "-" * 70)
    print("[Exercise 5] alert_family(): Direct alert dispatch")
    print("-" * 70)
    alert_res = alert_family(
        reason="Scheduled evening door sensor detected no movement",
        severity="medium",
    )
    print(f"Direct Alert Result:\n{json.dumps(alert_res, indent=2)}")

    print("\n" + "=" * 70)
    print(" CareLoop verification complete: All 5 tools exercised successfully!")
    print("=" * 70)
