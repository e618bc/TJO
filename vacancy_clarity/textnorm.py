"""Нормализация текста вакансий и разбиение на предложения.

В реальных вакансиях кириллические слова часто набраны с латинскими буквами-двойниками
(«Грaфик рaботы cменный»). Для поиска такие буквы заменяются кириллическими. Длина строки
при этом не меняется, поэтому позиции в нормализованном тексте совпадают с позициями в исходном
и цитата всегда берётся из исходного текста.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LOOKALIKE = str.maketrans("aceopxyAKMHOPCTXBE", "асеорхуАКМНОРСТХВЕ")
_LATIN_LOOKALIKE = set("aceopxyAKMHOPCTXBE")
_WORD = re.compile(r"[^\W\d_]+")
_CYR = re.compile(r"[а-яёА-ЯЁ]")


def norm(text: str) -> str:
    """Заменяет латинские буквы-двойники в словах, где есть кириллица, и в одиночных предлогах."""

    def fix(match: re.Match) -> str:
        word = match.group(0)
        if _CYR.search(word):
            return word.translate(_LOOKALIKE)
        # одиночные «c», «o», «a», «y» между русскими словами — это предлоги и союзы
        if len(word) == 1 and word in _LATIN_LOOKALIKE:
            around = text[max(0, match.start() - 12):match.end() + 12]
            if _CYR.search(around):
                return word.translate(_LOOKALIKE)
        return word

    return _WORD.sub(fix, text)


# Сокращения, после которых точка не заканчивает предложение.
_ABBR = {
    "г", "гг", "ул", "пос", "п", "д", "с", "стр", "корп", "кв", "зд", "оф", "каб", "пр", "пер", "обл", "р-н",
    "руб", "р", "т", "тыс", "млн", "коп", "мес", "мин", "час", "ч", "чел", "шт", "ед", "кат", "тел", "доб",
    "мед", "сан", "спец", "проф", "др", "пр", "им", "ст", "мл", "вед", "гл", "зам", "зав", "нач", "см", "напр",
    "т.д", "т.п", "т.е", "т.к", "н.п", "в/ч", "д/с", "пгт", "гор", "дер", "ср", "тех", "соц", "гос", "муз",
}
_BREAK = re.compile(r"([.!?;]+)(\s+)|(\s*[\n\r•]+\s*)")


@dataclass
class Sentence:
    n: int            # сквозной номер в вакансии, с единицы
    field: str        # путь к полю источника, например «duty» или «term.text»
    start: int        # позиция в значении поля
    end: int
    raw: str          # исходная подстрока поля
    text: str         # нормализованный текст той же длины

    def quote(self, limit: int = 200) -> str:
        """Цитата — исходная подстрока, при необходимости обрезанная по границе слова."""
        if len(self.raw) <= limit:
            return self.raw
        cut = self.raw.rfind(" ", 0, limit)
        return self.raw[: cut if cut > limit // 2 else limit]


def split_spans(text: str) -> list[tuple[int, int]]:
    """Границы предложений в тексте: список пар (начало, конец) без окружающих пробелов."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _BREAK.finditer(text):
        if m.group(1):
            before = text[start:m.start()]
            last = re.search(r"([^\s,;:()«»\"]+)$", before)
            token = (last.group(1) if last else "").lower().rstrip(".")
            nxt = text[m.end():m.end() + 1]
            is_abbr = token in _ABBR or (len(token) == 1 and token.isalpha())
            if m.group(1) == "." and (is_abbr or (nxt and nxt.islower())):
                continue
            end = m.start() + len(m.group(1))
        else:
            end = m.start()
        spans.append((start, end))
        start = m.end()
    spans.append((start, len(text)))
    out = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b - a >= 2 and re.search(r"\w", text[a:b]):
            out.append((a, b))
    return out


def sentences(field: str, value: str, first_n: int = 1) -> list[Sentence]:
    """Предложения одного поля с позициями в исходном значении."""
    normalized = norm(value)
    return [Sentence(first_n + i, field, a, b, value[a:b], normalized[a:b])
            for i, (a, b) in enumerate(split_spans(normalized))]
