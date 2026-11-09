"""Spot attempts to override the assistant's instructions ("prompt injection").

These patterns are deliberately specific: a customer saying "I can't ignore this error" or "ignore my
last email" is fine. Anything subtler is left to the router and the agents' own rules.
"""
import re
from typing import Optional

_TARGET = r"(?:instructions?|rules|prompts?|guidelines|directions|system message|programming|restrictions)"
PATTERNS = [
    ("override", re.compile(
        rf"\b(?:ignore|disregard|forget|override|bypass|skip)\b[\w\s,'-]{{0,40}}?\b(?:all|any|the|your|previous|prior|"
        rf"above|earlier|existing|original)\b[\w\s,'-]{{0,20}}?\b{_TARGET}", re.I)),
    ("reveal_prompt", re.compile(
        rf"\b(?:reveal|show|print|repeat|display|output|leak|dump|share|tell me|give me|what (?:is|are))\b"
        rf"[\w\s,'-]{{0,30}}?\b(?:system|hidden|initial|original|internal|secret|developer)\s+{_TARGET}", re.I)),
    ("system_prompt", re.compile(r"\bsystem\s*prompt\b", re.I)),
    ("role_change", re.compile(
        r"\b(?:you are now|from now on,? you (?:are|will)|pretend (?:to be|you are)|roleplay as|"
        r"act as (?:if you (?:are|were) )?(?:an? )?(?:unrestricted|unfiltered|jailbroken|different|evil|dan\b))", re.I)),
    ("jailbreak", re.compile(r"\b(?:jailbreak|developer mode|do anything now|dan mode|god mode)\b", re.I)),
    ("echo_above", re.compile(
        r"\b(?:repeat|print|output|copy|reproduce|write out)\b[\w\s,'-]{0,20}?\b(?:text|words|everything|all|"
        r"message|conversation)\b[\w\s,'-]{0,12}?\b(?:above|before this|so far)\b|\bstarting with [\"']?you are\b",
        re.I)),
    ("fake_role_tag", re.compile(r"(?:<\s*/?\s*(?:system|assistant)\s*>|^\s*(?:#{2,}|\[)\s*(?:system|assistant)\b)",
                                 re.I | re.M)),
]


def detect_injection(text: str) -> Optional[str]:
    """Name of the first matching pattern, or None."""
    for name, pattern in PATTERNS:
        if pattern.search(text):
            return name
    return None
