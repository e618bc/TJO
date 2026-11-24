"""Сводная таблица: отчёты нескольких движков против ключей наборов eval/blind/set_*.

    python eval/compare.py gemma clef            # имена из data/blind_<набор>_<движок>.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vacancy_clarity.evaluate import evaluate  # noqa: E402

SETS = [(s, f"eval/blind/set_{s}/key.json") for s in "ABCDE"]
for engine in sys.argv[1:]:
    t = dict.fromkeys(("must_found", "must_total", "fp_forbidden", "fp_unlisted", "clean_with_empty_report",
                       "clean_vacancies", "type_errors"), 0)
    for name, key in SETS:
        path = f"data/blind_{name}_{engine}.json"
        if not os.path.exists(path):
            continue
        r = evaluate(json.load(open(path, encoding="utf-8")), json.load(open(key, encoding="utf-8")))
        for k in t:
            t[k] += r[k]
        print(f"{engine:10s} {name}: найдено {r['must_found']:2d}/{r['must_total']:<2d} лишних {r['fp_forbidden']}+"
              f"{r['fp_unlisted']} чистые пустые {r['clean_with_empty_report']}/{r['clean_vacancies']} тип {r['type_errors']}")
        for m in r["misses"]:
            print(f"      пропуск {m[0]} {m[1]}")
        for fp in r["false_positive_list"]:
            print(f"      лишнее  {fp[0]} {fp[1]} {fp[2]} {fp[3]} «{fp[4]}»")
    fp = t["fp_forbidden"] + t["fp_unlisted"]
    if t["must_total"]:
        print(f"ИТОГО {engine}: полнота {t['must_found']}/{t['must_total']} = {t['must_found'] / t['must_total']:.2f}; "
              f"лишних {fp} (по запрещённым темам {t['fp_forbidden']}); точность "
              f"{t['must_found'] / max(1, t['must_found'] + fp):.2f}; чистые пустые {t['clean_with_empty_report']}/"
              f"{t['clean_vacancies']}; ошибок типа {t['type_errors']}\n")
