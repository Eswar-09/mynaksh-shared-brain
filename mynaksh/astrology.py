"""Deliberately simplified astrology helpers. Sun sign only; no ephemeris."""
from __future__ import annotations

from datetime import date

# (month, first day of the sign) in calendar order. Dates before Jan 20 fall back to Capricorn.
_CUTOFFS = [
    (1, 20, "Aquarius"),
    (2, 19, "Pisces"),
    (3, 21, "Aries"),
    (4, 20, "Taurus"),
    (5, 21, "Gemini"),
    (6, 21, "Cancer"),
    (7, 23, "Leo"),
    (8, 23, "Virgo"),
    (9, 23, "Libra"),
    (10, 23, "Scorpio"),
    (11, 22, "Sagittarius"),
    (12, 22, "Capricorn"),
]

SIGN_TRAITS = {
    "Aries": ("Fire", "Mars", "bold, driven, impatient"),
    "Taurus": ("Earth", "Venus", "steady, practical, stubborn"),
    "Gemini": ("Air", "Mercury", "curious, adaptable, restless"),
    "Cancer": ("Water", "Moon", "nurturing, intuitive, protective"),
    "Leo": ("Fire", "Sun", "confident, generous, wants recognition"),
    "Virgo": ("Earth", "Mercury", "analytical, diligent, self-critical"),
    "Libra": ("Air", "Venus", "diplomatic, fair-minded, indecisive"),
    "Scorpio": ("Water", "Mars/Pluto", "intense, focused, private"),
    "Sagittarius": ("Fire", "Jupiter", "optimistic, independent, blunt"),
    "Capricorn": ("Earth", "Saturn", "disciplined, ambitious, reserved"),
    "Aquarius": ("Air", "Saturn/Uranus", "inventive, independent, detached"),
    "Pisces": ("Water", "Jupiter/Neptune", "empathetic, imaginative, escapist"),
}


def sun_sign(d: date) -> str:
    sign = "Capricorn"
    for month, day, name in _CUTOFFS:
        if (d.month, d.day) >= (month, day):
            sign = name
    return sign


def describe_sign(sign: str) -> str:
    element, ruler, traits = SIGN_TRAITS.get(sign, ("?", "?", ""))
    return f"{sign} ({element} sign, ruled by {ruler}; {traits})"
