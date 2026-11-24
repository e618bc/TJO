"""Анализ набора вакансий, общий для рабочего и проверочного режима."""
from __future__ import annotations

import datetime
import json
import os
import shutil
import time
from pathlib import Path

from . import BANK_VERSION, __version__
from . import facts as F
from .ai import AIError, EngineChain
from .decide import decide
from .ingest import dedup
from .questions import build_questions, render_state


def policy_from(cfg) -> dict:
    return {
        "wide_range_ratio": cfg.float("policy", "wide_range_ratio"),
        "hint_range_ratio": cfg.float("policy", "hint_range_ratio"),
        "pay_conflict": cfg.float("policy", "pay_conflict"),
        "pay_hint": cfg.float("policy", "pay_hint"),
        "pay_conflict_confirmed": cfg.float("policy", "pay_conflict_confirmed"),
        "full_time_hours": cfg.float("policy", "full_time_hours"),
        "min_monthly_pay": cfg.int("policy", "min_monthly_pay"),
    }


def _strings(obj):
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)
    elif obj is not None:
        yield str(obj)


def quotes_are_verbatim(issue: dict, vacancy: dict) -> bool:
    """Страховка от выдуманных цитат: каждая цитата должна дословно встречаться в исходной вакансии."""
    values = list(_strings(vacancy))
    return all(not q or any(q in v for v in values) for q in (issue.get("quote", ""), issue.get("quote_2", "")))


def analyze_one(vacancy: dict, chain: EngineChain, policy: dict, with_hints: bool) -> dict:
    """Запись отчёта по одной вакансии. Сбой ИИ не мешает замечаниям, которые код установил сам."""
    f = F.build(vacancy)
    questions = build_questions(f)
    answers, ai_status, engine = None, "off", ""
    if chain and questions:
        try:
            answers, engine = chain.ask(render_state(f), questions)
            ai_status = "ok"
        except AIError as e:
            ai_status = f"failed: {str(e)[:200]}"
    elif chain:
        answers, ai_status = {}, "not_needed"
    issues, hints = decide(f, answers, policy)
    issues = [it for it in issues if quotes_are_verbatim(it, vacancy)]
    record = {"id": f.vid, "url": f.url, "job_name": f.title, "issues": issues}
    if with_hints:
        record["hints"] = hints
    record["ai"] = {"status": ai_status, "engine": engine, "answers": answers or {}}
    return record


def analyze(vacancies: list, chain: EngineChain, cfg, source: dict, log=print) -> dict:
    """Строит отчёт по списку вакансий: одна запись на ID."""
    started = time.time()
    unique, skipped = dedup(vacancies)
    policy = policy_from(cfg)
    with_hints = cfg.bool("report", "hints")
    records = []
    for i, vacancy in enumerate(unique, 1):
        t0 = time.time()
        try:
            record = analyze_one(vacancy, chain, policy, with_hints)
        except Exception as e:                      # одна испорченная вакансия не должна ронять отчёт
            record = {"id": str(vacancy.get("id", "")), "url": str(vacancy.get("vac_url", "") or ""),
                      "job_name": str(vacancy.get("job-name", "")), "issues": [],
                      "ai": {"status": "off", "engine": "", "answers": {}},
                      "error": f"{type(e).__name__}: {e}"}
            if with_hints:
                record["hints"] = []
        records.append(record)
        if len(unique) <= 20 or i % 10 == 0 or i == len(unique):
            spent = time.time() - started
            eta = spent / i * (len(unique) - i)
            log(f"Анализ: {i}/{len(unique)} ({time.time() - t0:.1f} с на последнюю, осталось ~{eta:.0f} с)")
    ai_ok = sum(1 for r in records if r["ai"]["status"] == "ok")
    ai_failed = sum(1 for r in records if r["ai"]["status"].startswith("failed"))
    now = datetime.datetime.now().astimezone()
    return {
        "meta": {
            "generated_at": now.isoformat(timespec="seconds"),
            "tool": f"vacancy_clarity {__version__}",
            "source": source,
            "vacancies_received": len(vacancies),
            "vacancies_in_report": len(records),
            "skipped": skipped,
            "with_issues": sum(1 for r in records if r["issues"]),
            "issues_total": sum(len(r["issues"]) for r in records),
            "ai": {
                "status": "off" if not chain else "ok" if not ai_failed else "partial" if ai_ok else "failed",
                "question_bank": BANK_VERSION,
                "engines": chain.describe() if chain else [],
                "vacancies_answered": ai_ok,
                "vacancies_failed": ai_failed,
            },
            "duration_seconds": round(time.time() - started, 1),
        },
        "vacancies": records,
    }


def write_report(report: dict, path: str, history: bool = True) -> str:
    """Атомарная запись: отчёт сначала пишется во временный файл, затем подменяет прежний."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    if history:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        archive = target.parent / "history"
        archive.mkdir(exist_ok=True)
        shutil.copyfile(target, archive / f"{target.stem}_{stamp}{target.suffix}")
    return str(target)
