"""Stage 1 of memory: deterministic extraction of explicit profile facts.

Profile fields are structured, high-value and easy to parse, so they never need
an LLM call. Anything matched here overwrites the stored value (a correction).
"""
from __future__ import annotations

import re
from datetime import date, time
from typing import Any

from mynaksh.astrology import sun_sign
from mynaksh.models import MemoryUpdate, UserProfile, utcnow

_MONTHS = {
    m: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for m in names
}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
LANGUAGES = ("hindi", "english", "hinglish", "tamil", "telugu", "marathi", "bengali",
             "gujarati", "kannada", "malayalam", "punjabi", "odia")
_NOT_NAMES = {"not", "actually", "really", "just", "also", "so", "here", "the", "a"}

_DATE_PATTERNS = [
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\.?,?\s+(\d{{4}})", re.I), "dmy"),
    (re.compile(rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I), "mdy"),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "iso"),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"), "dmy_num"),  # Indian convention
]


def _parse_date(text: str) -> date | None:
    for pattern, kind in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if kind == "dmy":
                d = date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
            elif kind == "mdy":
                d = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
            elif kind == "iso":
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            else:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except (ValueError, KeyError):
            continue
        if date(1900, 1, 1) <= d <= date.today():
            return d
    return None


def extract_profile_facts(message: str) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    low = message.lower()

    m = re.search(r"\b(?:[Mm]y name is|[Cc]all me|[Ii] am called)\s+(?:actually\s+)?([A-Za-z][A-Za-z'-]+)(\s+[A-Z][A-Za-z'-]+)?", message)
    if m and m.group(1).lower() not in _NOT_NAMES:
        facts["name"] = (m.group(1) + (m.group(2) or "")).strip().title()

    birth_ctx = re.search(r"\b(born|birthday|date of birth|dob|birth date)\b", low)
    if birth_ctx:
        tail = message[birth_ctx.start():]
        if d := _parse_date(tail):
            facts["date_of_birth"] = d

        if tm := re.search(r"\b(?:at|around|time of birth is)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", tail, re.I):
            hour, minute = int(tm.group(1)), int(tm.group(2) or 0)
            ampm = (tm.group(3) or "").lower().replace(".", "")
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            if (tm.group(2) or ampm) and 0 <= hour < 24 and 0 <= minute < 60:
                facts["time_of_birth"] = time(hour, minute)

        if pm := re.search(r"\bborn\b[^.?!]*?\bin\s+([A-Z][a-zA-Z]+(?:[ -][A-Z][a-zA-Z]+)*)", message):
            facts["birth_place"] = pm.group(1)

    if lm := re.search(r"\b(?:reply|respond|answer|talk|speak|write|chat|prefer)\b[^.?!]*?\b(" + "|".join(LANGUAGES) + r")\b", low):
        facts["preferred_language"] = lm.group(1).title()
    return facts


def apply_profile_facts(profile: UserProfile, facts: dict[str, Any]) -> tuple[UserProfile, list[MemoryUpdate]]:
    updates: list[MemoryUpdate] = []
    data = profile.model_dump()
    for field, new in facts.items():
        old = data.get(field)
        if new == old:
            continue
        data[field] = new
        updates.append(
            MemoryUpdate(action="profile_updated", key=field, value=str(new),
                         previous_value=str(old) if old is not None else None)
        )
    if not updates:
        return profile, []
    if data.get("date_of_birth"):
        sign = sun_sign(data["date_of_birth"])
        if sign != data.get("zodiac_sign"):
            updates.append(MemoryUpdate(action="profile_updated", key="zodiac_sign", value=sign,
                                        previous_value=data.get("zodiac_sign")))
            data["zodiac_sign"] = sign
    data["updated_at"] = utcnow()
    return UserProfile.model_validate(data), updates
