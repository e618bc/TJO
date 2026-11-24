#!/usr/bin/env python3
"""Собирает размеченный набор из описаний случаев и проверяет разметку.

    python3 testset/build_testset.py

Вход: cases_base.py, cases_extra.py (вымышленные вакансии), cases_real.py и real_vacancies.json (реальные).
Выход: vacancies.json, expected.json, expected_report.json, real_expected.json, structural/,
docs/rule-examples.md. Критерии разметки — docs/criteria.md.
"""
import copy
import json
import random
import re
from pathlib import Path

import cases_base  # noqa: F401  (регистрирует случаи)
import cases_extra  # noqa: F401  (регистрирует случаи)
from cases_real import REAL
from lib import CASES, FLEX, FULL, PART, ROTA, RULES, SHIFT, TOPICS, TYPES, build, norm

HERE = Path(__file__).parent

# Формулировки, недопустимые в полях «проблема» и «рекомендация» (к цитатам не применяются).
FORBIDDEN = [r"нарушен", r"незаконн", r"противозаконн", r"по закону", r"закон\w* (запрещ|обязыва|требу)",
             r"\bТК\b", r"Трудов\w+ кодекс", r"\bст\.\s*\d+", r"\bМРОТ\b", r"дискриминац", r"штраф",
             r"\bобязан[аоы]?\b", r"недопустим", r"занижен", r"ниже рынка", r"скорее всего", r"вероятно"]

OPEN_SCHEDULES = (FLEX, SHIFT, PART, ROTA)

SCORING = [
    "Замечание засчитывается пункту must, если совпала тема (любая из перечисленных).",
    "Для противоречия в замечании должны быть цитаты из обеих групп quotes_all.",
    "Типы missing и ambiguity считаются одним классом «неполнота»; ошибка типа — только путаница с contradiction.",
    "Пункт must без замечания — пропуск.",
    "Замечание по теме из clean_topics — ложное срабатывание.",
    "Темы из optional в счёт не идут; поле lean показывает наш выбор по умолчанию (flag — выводим, skip — нет).",
    "Тексты problem и recommendation не должны совпадать с forbidden_patterns.",
]

SCHEDULE_PARAMS = re.compile(
    r"\d{1,2}\s*[:.\-]\s*\d{2}"                     # 9:00, 08.00, 8-00
    r"|\bс\s+\d{1,2}\s+до\s+\d{1,2}\b"              # с 9 до 18
    r"|\b\d{1,2}\s*/\s*\d{1,2}\b"                   # 5/2, 2/2, 15/15
    r"|сутки через|\d+\s*[- ]?\s*час"               # сутки через трое, 12 часов, 20 часовая
    r"|дневн\w*\s+(рабоч\w+\s+)?недел"              # пятидневная (рабочая) неделя
    r"|понедельник|пн\s*[–-]\s*пт|выходн", re.I)


def strings(o):
    if isinstance(o, dict):
        for x in o.values():
            yield from strings(x)
    elif isinstance(o, list):
        for x in o:
            yield from strings(x)
    elif o is not None:
        yield str(o)


def raw_quote(vacancy, quote):
    """Находит цитату в вакансии с учётом букв-двойников и возвращает исходную подстроку."""
    q = norm(quote)
    for s in strings(vacancy):
        i = norm(s).find(q)
        if i >= 0:
            return s[i:i + len(q)]
    return None


def check_must(label, vacancy, m):
    """Проверяет пункт эталона и заменяет цитаты исходными подстроками из вакансии."""
    assert m["rule"] in RULES, f"{label}: неизвестное правило {m['rule']}"
    assert set(m["topic"]) <= set(TOPICS), f"{label}: тема {m['topic']}"
    assert set(m["type"]) <= set(TYPES), f"{label}: тип {m['type']}"
    assert RULES[m["rule"]][0] in m["topic"] or m["rule"] in ("TTL-4", "SCH-1", "EMP-1"), \
        f"{label}: тема правила {m['rule']} не входит в темы пункта {m['topic']}"
    has_quotes = bool(m["quotes_all"] or m["quotes_any"])
    if m["type"] == ["missing"]:
        assert not has_quotes, f"{label}/{m['rule']}: у пропуска цитата должна быть пустой"
    if "missing" not in m["type"]:
        assert has_quotes, f"{label}/{m['rule']}: нужна цитата"
    if m["type"] == ["contradiction"]:
        assert len(m["quotes_all"]) >= 2, f"{label}/{m['rule']}: у противоречия нужны две группы цитат"
    for text in (m["problem"], m["recommendation"]):
        for pat in FORBIDDEN:
            assert not re.search(pat, text, re.I), f"{label}/{m['rule']}: запрещённая формулировка {pat!r}"

    def resolve(q):
        raw = raw_quote(vacancy, q)
        assert raw is not None, f"{label}/{m['rule']}: цитаты нет в вакансии: {q!r}"
        return raw

    m = dict(m)
    m["quotes_all"] = [[resolve(q) for q in g] for g in m["quotes_all"]]
    m["quotes_any"] = [resolve(q) for q in m["quotes_any"]]
    return m


def lint(label, v, allowed):
    """Единообразие разметки массовых спорных случаев."""
    req = v.get("requirement") or {}
    text = norm(" ".join(str(x) for x in (
        v.get("job-name"), v.get("duty"), v.get("requirements"), v.get("scheduleTypeComment"),
        req.get("qualification"), (v.get("term") or {}).get("text")) if x))
    if v.get("schedule") == FULL and not SCHEDULE_PARAMS.search(text):
        assert "schedule" in allowed, f"{label}: полный день без часов и дней — нужен must или optional по графику"
    if v.get("schedule") in OPEN_SCHEDULES and not SCHEDULE_PARAMS.search(text):
        assert "schedule" in allowed, f"{label}: открытый режим без параметров — нужен must или optional"
    if not v.get("employment") and not re.search(r"ставк|совместительств", text, re.I):
        assert "employment" in allowed, f"{label}: нет поля занятости — нужен must или optional"
    if not v.get("salary_min") and not re.search(r"\d[\d ]{3,}\s*руб", text):
        assert "salary" in allowed, f"{label}: суммы нет — нужен must или optional по оплате"


def label_row(label, v, intent, musts, optional, neg):
    for topic, o in optional.items():
        assert topic in TOPICS and o["rule"] in RULES, f"{label}: спорная тема {topic}/{o['rule']}"
    for r in neg:
        assert r in RULES, f"{label}: неизвестное правило в neg: {r}"
    musts = [check_must(label, v, m) for m in musts]
    must_topics = {t for m in musts for t in m["topic"]}
    allowed = must_topics | set(optional)
    lint(label, v, allowed)
    for r in neg:
        assert RULES[r][0] not in {m["topic"][0] for m in musts if m["rule"] == r}, \
            f"{label}: правило {r} одновременно в must и в neg"
    return {
        "id": v["id"], "case": label, "intent": intent,
        "must": musts,
        "optional": [{"topic": t, **o} for t, o in optional.items()],
        "clean_topics": [t for t in TOPICS if t not in allowed],
        "negative_for": list(neg),
    }


def report_issue(m):
    """Замечание идеального отчёта в формате задания."""
    quotes = [g[0] for g in m["quotes_all"]] or m["quotes_any"][:1]
    if len(m["quotes_all"]) >= 2 and "contradiction" in m["type"]:
        issue = {"topic": m["topic"][0], "type": "contradiction", "rule": m["rule"],
                 "field": m["fields"][0], "quote": quotes[0],
                 "field_2": m["fields"][1] if len(m["fields"]) > 1 else m["fields"][0], "quote_2": quotes[1]}
    elif quotes:
        kind = "ambiguity" if "ambiguity" in m["type"] else next(t for t in m["type"] if t != "missing")
        issue = {"topic": m["topic"][0], "type": kind, "rule": m["rule"], "field": m["fields"][0], "quote": quotes[0]}
    else:
        issue = {"topic": m["topic"][0], "type": "missing", "rule": m["rule"], "field": m["fields"][0], "quote": ""}
    return {**issue, "problem": m["problem"], "recommendation": m["recommendation"]}


def wrap(vacancies):
    return {"status": "200", "request": {"api": "v1"}, "meta": {"total": len(vacancies), "limit": 100},
            "results": {"vacancies": [{"vacancy": v} for v in vacancies]}}


def expected_doc(rows):
    return {
        "rules": {k: {"topic": t, "kind": kind, "title": title} for k, (t, kind, title) in RULES.items()},
        "topics": TOPICS, "types": TYPES, "forbidden_patterns": FORBIDDEN, "scoring": SCORING,
        "vacancies": rows,
    }


def dump(path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def coverage(all_rows):
    """У обязательного правила должны быть пример с замечанием и похожий без; у спорного — спорный пример."""
    pos = {r: [] for r in RULES}
    disputed = {r: [] for r in RULES}
    negs = {r: [] for r in RULES}
    for row in all_rows:
        for m in row["must"]:
            pos[m["rule"]].append(row["case"])
        for o in row["optional"]:
            disputed[o["rule"]].append(row["case"])
        for r in row["negative_for"]:
            negs[r].append(row["case"])
    print("\nПокрытие правил (с замечанием / спорные / похожие без замечания):")
    problems = []
    for r, (_, kind, title) in RULES.items():
        print(f"  {r} {kind:4s} {len(pos[r]):2d} / {len(disputed[r]):2d} / {len(negs[r]):2d}  {title}")
        if kind == "must" and not pos[r]:
            problems.append(f"{r}: нет примера с замечанием")
        if kind == "must" and not negs[r]:
            problems.append(f"{r}: нет отрицательного примера")
        if kind != "must" and not disputed[r]:
            problems.append(f"{r}: нет спорного примера")
    assert not problems, "; ".join(problems)

    def codes(items):
        return ", ".join(sorted(set(items))) or "—"

    lines = ["# Правила и примеры к ним", "",
             "Собирается `testset/build_testset.py`. Коды вида `G03` — вымышленные вакансии, "
             "восьмизначные (`fef47018`) — реальные.", "",
             "| Правило | Вердикт | Что проверяет | С замечанием | Спорные | Похожие без замечания |",
             "|---|---|---|---|---|---|"]
    verdict = {"must": "замечание", "flag": "спорно, выводим", "skip": "спорно, не выводим"}
    for r, (_, kind, title) in RULES.items():
        lines.append(f"| {r} | {verdict[kind]} | {title} | {codes(pos[r])} | {codes(disputed[r])} | {codes(negs[r])} |")
    (HERE.parent / "docs" / "rule-examples.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def structural():
    """Входы для проверки устойчивости и ожидаемое поведение."""
    by_code = {c["code"]: c for c in CASES}
    clean = build(by_code["A01"])
    flex_bad = build(by_code["G01"])
    flex_fixed = copy.deepcopy(flex_bad)
    flex_fixed["scheduleTypeComment"] = "понедельник–пятница, начало с 8:00 до 10:00, 8-часовой рабочий день"
    flex_fixed["date_modify"] = "2026-11-22T09:00:00+0300"

    minimal = {"id": "7e570000-0000-4000-8000-00000000a001", "job-name": "Дворник",
               "duty": "Уборка территории двора, очистка урн."}
    odd_types = copy.deepcopy(clean)
    odd_types.update({"id": "7e570000-0000-4000-8000-00000000a002", "salary_min": "45000", "salary_max": None,
                      "shift": None, "skills": None, "term": None, "unknown_field": {"x": 1}})
    odd_types["vac_url"] = f"https://trudvsem.ru/vacancy/card/0000000000000/{odd_types['id']}"
    no_url = copy.deepcopy(clean)
    no_url["id"] = "7e570000-0000-4000-8000-00000000a003"
    no_url.pop("vac_url")
    no_id = copy.deepcopy(clean)
    no_id.pop("id")
    no_id.pop("vac_url")

    out = HERE / "structural"
    dump(out / "empty.json", wrap([]))
    dump(out / "error_500.json", {"status": "500", "request": {"api": "v1"}, "meta": {
        "error": "При выполнении запроса произошла ошибка. Проверьте синтаксис запроса и\\или обратитесь в службу поддержки"}})
    dump(out / "duplicate_ids.json", wrap([flex_fixed, flex_bad]))
    dump(out / "sparse.json", wrap([minimal, odd_types, no_url, no_id]))
    dump(out / "expected.json", {
        "empty.json": {"report_ids": [], "note": "Валидный отчёт с пустым списком вакансий."},
        "error_500.json": {"report_ids": None,
                           "note": "Сообщение об ошибке; новый отчёт не создаётся, последний успешный остаётся."},
        "duplicate_ids.json": {
            "report_ids": [flex_fixed["id"]],
            "note": "Одна запись на ID. Берётся вариант с более поздним date_modify (он идёт первым), "
                    "поэтому замечания по графику быть не должно."},
        "sparse.json": {
            "report_ids": [minimal["id"], odd_types["id"], no_url["id"]],
            "note": "Вакансия без id пропускается с сообщением. Отсутствующие поля считаются уровнем «нет», "
                    "null и число строкой не приводят к сбою, неизвестные поля игнорируются, "
                    "при отсутствии vac_url ссылка пустая."},
    })


def main():
    assert len({c["code"] for c in CASES}) == len(CASES), "коды случаев должны быть уникальны"

    rows = [(c, build(c)) for c in CASES]
    random.Random(39).shuffle(rows)
    synth = [label_row(c["code"], v, c["intent"], c["must"], c["optional"], c["neg"]) for c, v in rows]
    dump(HERE / "vacancies.json", wrap([v for _, v in rows]))
    dump(HERE / "expected.json", expected_doc(synth))
    dump(HERE / "expected_report.json", {"vacancies": [
        {"id": v["id"], "url": v.get("vac_url", ""), "issues": [report_issue(m) for m in row["must"]]}
        for (_, v), row in zip(rows, synth)]})

    real_vs = [x["vacancy"] for x in json.loads((HERE / "real_vacancies.json").read_text(encoding="utf-8"))
               ["results"]["vacancies"]]
    by_prefix = {v["id"][:8]: v for v in real_vs}
    assert set(by_prefix) == set(REAL), f"разметка и файл расходятся: {set(by_prefix) ^ set(REAL)}"
    real = [label_row(p, by_prefix[p], r["intent"], r.get("must", []), r.get("optional", {}), r.get("neg", []))
            for p, r in REAL.items()]
    dump(HERE / "real_expected.json", expected_doc(real))

    structural()

    for name, part in (("синтетика", synth), ("реальные", real)):
        n_must = sum(len(r["must"]) for r in part)
        only_opt = sum(1 for r in part if not r["must"] and r["optional"])
        clean = sum(1 for r in part if not r["must"] and not r["optional"])
        types = {t: sum(report_issue(m)["type"] == t for r in part for m in r["must"]) for t in TYPES}
        print(f"{name}: вакансий {len(part)}; с обязательными замечаниями {sum(1 for r in part if r['must'])} "
              f"(замечаний {n_must}: {types}); только спорные {only_opt}; чистые {clean}")
    coverage(synth + real)


if __name__ == "__main__":
    main()
