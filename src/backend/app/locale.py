"""Per-turn language policy, independent of persona and session identity."""

from typing import Literal

Locale = Literal["en", "nl"]


def validate_locale(value: object) -> Locale | None:
    if value is None or value == "en" or value == "nl":
        return value
    raise ValueError("locale must be 'en' or 'nl'")


def localize_turn(message: str, locale: Locale | None) -> str:
    if locale is None:
        return message
    language = "Dutch" if locale == "nl" else "English"
    return (
        f'<response_locale code="{locale}">\n'
        f"For this turn only, use {language} as the default output language. "
        "An explicit output-language request in the user's message takes precedence for that output. "
        "Do not translate previous messages or source documents unless requested. "
        "Language does not change the jurisdiction or the persona's capabilities.\n"
        f"</response_locale>\n\n{message}"
    )
