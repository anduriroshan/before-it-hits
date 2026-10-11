"""The agent layer: ask about a box in plain language, and get an answer nothing invented.

The engine produces findings. These agents decide what to ask it, write the answer up, and check
that write-up against the findings before anyone sees it. No agent produces a figure.
"""
from .llm import CAREFUL_MODEL, FAST_MODEL, LlmUnavailable, available

__all__ = ["available", "LlmUnavailable", "FAST_MODEL", "CAREFUL_MODEL"]
