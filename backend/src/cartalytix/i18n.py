from functools import lru_cache
from gettext import GNUTranslations, NullTranslations
from io import BytesIO
from pathlib import Path

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

_LOCALES = Path(__file__).parent / "locales"


@lru_cache(maxsize=2)
def catalog(language: str) -> GNUTranslations | NullTranslations:
    source = _LOCALES / language / "LC_MESSAGES" / "messages.po"
    if not source.is_file():
        return NullTranslations()
    with source.open("rb") as stream:
        messages = read_po(stream, locale=language)
    compiled = BytesIO()
    write_mo(compiled, messages)
    compiled.seek(0)
    return GNUTranslations(compiled)


def translate(message: str, locale: str = "en") -> str:
    language = "cs" if locale.lower().startswith("cs") else "en"
    return catalog(language).gettext(message)
