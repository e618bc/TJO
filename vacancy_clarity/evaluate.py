"""Сверка отчёта с ключом ответов.

Понимает два формата ключа:
- наш эталон (testset/expected.json): must / optional / clean_topics;
- ключ внешнего автора (eval/blind/*/key.json): expected с importance core|accept и must_not_flag.

Замечание засчитывается пункту ключа по теме. Типы «пропуск» и «неоднозначность» считаются одним
классом; для противоречия дополнительно проверяется, что в замечании процитированы обе стороны.
"""
from __future__ import annotations

import json
from collections import Counter

TOPICS = ["title", "duties", "requirements", "salary", "schedule", "location", "employment"]


def _norm_key(key: dict) -> dict:
    """Приводит ключ к виду {id: {"must": [...], "optional": {темы}, "forbidden": {темы}, "label": str}}."""
    out = {}
    for row in key["vacancies"]:
        if "expected" in row or "must_not_flag" in row or "is_clean" in row:      # ключ внешнего автора
            must, optional = [], set()
            for e in row.get("expected", []):
                topics = [e["topic"]] if isinstance(e["topic"], str) else list(e["topic"])
                if e.get("importance", "core") == "core":
                    must.append({"topic": topics, "type": [e.get("type", "")], "quotes": e.get("quotes", []),
                                 "rule": "", "note": e.get("note", "")})
                else:
                    optional.update(topics)
            listed = {t for m in must for t in m["topic"]} | optional
            forbidden = {x["topic"] for x in row.get("must_not_flag", []) if x.get("topic") in TOPICS} - listed
            out[str(row["id"])] = {"must": must, "optional": optional, "forbidden": forbidden,
                                   "unlisted": set(TOPICS) - listed - forbidden, "label": str(row["id"])}
        else:                                                                     # наш эталон
            must = [{"topic": m["topic"], "type": m["type"], "rule": m.get("rule", ""), "note": m.get("problem", ""),
                     "quotes": [q for g in m.get("quotes_all", []) for q in g[:1]]} for m in row["must"]]
            optional = {o["topic"] for o in row["optional"]}
            out[str(row["id"])] = {"must": must, "optional": optional, "forbidden": set(row["clean_topics"]),
                                   "unlisted": set(), "label": row.get("case", row["id"])}
    return out


def evaluate(report: dict, key: dict) -> dict:
    keyed = _norm_key(key)
    records = {str(r["id"]): r for r in report["vacancies"]}
    stats = Counter()
    misses, false_pos, type_errors, absent = [], [], [], []
    for vid, k in keyed.items():
        rec = records.get(vid)
        if rec is None:
            absent.append(k["label"])
            stats["must"] += len(k["must"])
            stats["missed"] += len(k["must"])
            continue
        issues = rec["issues"]
        used = set()
        for m in k["must"]:
            stats["must"] += 1
            want_contra = m["type"] == ["contradiction"]
            same_topic = [i for i, it in enumerate(issues) if it["topic"] in m["topic"]]
            hit = next((i for i in same_topic if (issues[i]["type"] == "contradiction") == want_contra),
                       same_topic[0] if same_topic else None)
            if hit is None:
                stats["missed"] += 1
                misses.append((k["label"], "/".join(m["topic"]), m.get("rule", ""), m.get("note", "")[:90]))
                continue
            stats["found"] += 1
            used.update(i for i, it in enumerate(issues) if it["topic"] in m["topic"])
            got = issues[hit]["type"]
            allowed = {"contradiction" if t == "contradiction" else "incomplete" for t in m["type"] if t}
            if allowed and ("contradiction" if got == "contradiction" else "incomplete") not in allowed:
                type_errors.append((k["label"], issues[hit]["topic"], f"ждали {'/'.join(m['type'])}, получили {got}"))
        clean_expected = not k["must"] and not k["optional"]
        stats["clean_vacancies"] += clean_expected
        fp_here = 0
        for i, it in enumerate(issues):
            if i in used or it["topic"] in k["optional"]:
                continue
            kind = "forbidden" if it["topic"] in k["forbidden"] else "unlisted"
            stats[f"fp_{kind}"] += 1
            fp_here += 1
            false_pos.append((k["label"], it["topic"], it["rule"], kind, it["quote"][:60]))
        if clean_expected and not issues:
            stats["clean_empty"] += 1
        stats["ai_failed"] += rec.get("ai", {}).get("status", "").startswith("failed")
    fp = stats["fp_forbidden"] + stats["fp_unlisted"]
    return {
        "vacancies": len(keyed),
        "must_total": stats["must"], "must_found": stats["found"],
        "recall": round(stats["found"] / stats["must"], 3) if stats["must"] else None,
        "false_positives": fp, "fp_forbidden": stats["fp_forbidden"], "fp_unlisted": stats["fp_unlisted"],
        "precision": round(stats["found"] / (stats["found"] + fp), 3) if stats["found"] + fp else None,
        "clean_vacancies": stats["clean_vacancies"], "clean_with_empty_report": stats["clean_empty"],
        "type_errors": len(type_errors), "ai_failed": stats["ai_failed"], "absent_in_report": absent,
        "misses": misses, "false_positive_list": false_pos, "type_error_list": type_errors,
    }


def format_result(res: dict, details: bool = True) -> str:
    lines = [
        f"Вакансий в ключе: {res['vacancies']}",
        f"Обязательные замечания: найдено {res['must_found']} из {res['must_total']} (полнота {res['recall']})",
        f"Лишние замечания: {res['false_positives']} (по запрещённым темам {res['fp_forbidden']}, "
        f"по неупомянутым {res['fp_unlisted']}); точность {res['precision']}",
        f"Чистые вакансии с пустым отчётом: {res['clean_with_empty_report']} из {res['clean_vacancies']}",
        f"Ошибки типа (противоречие против неполноты): {res['type_errors']}; сбоев ИИ: {res['ai_failed']}",
    ]
    if res["absent_in_report"]:
        lines.append(f"Нет в отчёте: {', '.join(res['absent_in_report'])}")
    if details:
        if res["misses"]:
            lines.append("\nПропущено:")
            lines += [f"  {a:14s} {b:22s} {c:6s} {d}" for a, b, c, d in res["misses"]]
        if res["false_positive_list"]:
            lines.append("\nЛишнее:")
            lines += [f"  {a:14s} {b:13s} {c:6s} {d:9s} {e}" for a, b, c, d, e in res["false_positive_list"]]
        if res["type_error_list"]:
            lines.append("\nТип:")
            lines += [f"  {a:14s} {b:13s} {c}" for a, b, c in res["type_error_list"]]
    return "\n".join(lines)


def run(report_path: str, key_path: str, details: bool = True) -> dict:
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    with open(key_path, encoding="utf-8") as fh:
        key = json.load(fh)
    res = evaluate(report, key)
    print(format_result(res, details))
    return res
