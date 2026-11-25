from __future__ import annotations

import re

_CONFIRM_PATTERNS = (
    r"^\s*(да|подтверждаю|подтверди|согласен|согласна|ок|окей|отправляй|кидай)\b",
    r"\b(да,?\s*отправь|подтверждаю\s+отправку|отправь\s+его|отправь\s+уже)\b",
)
_CANCEL_PATTERNS = (
    r"^\s*(отмена|отмени|не\s+надо|удали\s+черновик)\b",
    r"\b(не\s+отправляй|отмени\s+отправку)\b",
)


def is_confirmation(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return any(re.search(pattern, normalized) for pattern in _CONFIRM_PATTERNS)


def is_cancellation(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return any(re.search(pattern, normalized) for pattern in _CANCEL_PATTERNS)
