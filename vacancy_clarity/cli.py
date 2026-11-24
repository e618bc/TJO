"""Командная строка.

    python -m vacancy_clarity run                 рабочий режим: API → отчёт (то же делает расписание)
    python -m vacancy_clarity check FILE          проверочный режим: локальный JSON → отчёт, без обращения к порталу
    python -m vacancy_clarity schedule            запуск по расписанию из настроек
    python -m vacancy_clarity eval REPORT KEY     сверка отчёта с ключом ответов
    python -m vacancy_clarity doctor              проверка настроек и доступности ИИ
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

from . import __version__, evaluate
from .ai import make_chain
from .config import Config
from .ingest import SourceError, fetch_api, load_local
from .pipeline import analyze, write_report


def log(message: str) -> None:
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def _config(args) -> Config:
    overrides = {
        "ai.engine": "none" if getattr(args, "no_ai", False) else getattr(args, "engine", None),
        "source.url": getattr(args, "url", None),
        "source.max_vacancies": getattr(args, "max", None),
        "report.dir": getattr(args, "dir", None),
    }
    return Config(args.config, overrides)


def _summary(report: dict, path: str) -> None:
    m = report["meta"]
    engines = ", ".join(f"{e['engine']} ({e['model']})" for e in m["ai"]["engines"]) or "выключен"
    log(f"Отчёт: {path}")
    log(f"Вакансий в отчёте: {m['vacancies_in_report']}, с замечаниями: {m['with_issues']}, "
        f"замечаний: {m['issues_total']}; пропущено записей: {len(m['skipped'])}")
    log(f"ИИ: {m['ai']['status']} — {engines}; ответов {m['ai']['vacancies_answered']}, сбоев {m['ai']['vacancies_failed']}")


def print_report(report: dict, stream=None) -> None:
    """Отчёт в читаемом виде: по каждой вакансии — замечания с цитатами и рекомендациями."""
    stream = stream or sys.stdout
    out = []
    for rec in report["vacancies"]:
        out.append(f"\n{rec['id']} · {rec.get('job_name', '')}".rstrip(" ·"))
        if rec.get("url"):
            out.append(f"  {rec['url']}")
        if not rec["issues"]:
            out.append("  замечаний нет")
        for it in rec["issues"]:
            out.append(f"  [{it.get('topic_title', it['topic'])} · {it.get('type_title', it['type'])}] {it['problem']}")
            if it.get("quote"):
                out.append(f"    {it['field']}: «{it['quote']}»")
            else:
                out.append(f"    поле {it['field']}: сведений нет")
            if it.get("quote_2"):
                out.append(f"    {it['field_2']}: «{it['quote_2']}»")
            out.append(f"    Уточнить: {it['recommendation']}")
    m = report["meta"]
    out.append(f"\nИтого: {m['vacancies_in_report']} вакансий, с замечаниями {m['with_issues']}, "
               f"замечаний {m['issues_total']}.")
    print("\n".join(out), file=stream)


def cmd_run(args) -> int:
    """Рабочий режим. При ошибке источника прежний отчёт не меняется, код возврата 1."""
    cfg = _config(args)
    out = args.out or str(Path(cfg.get("report", "dir")) / "report.json")
    url = cfg.get("source", "url")
    try:
        vacancies, info = fetch_api(url, cfg.int("source", "max_vacancies"), cfg.int("source", "page_size"),
                                    cfg.float("source", "timeout_seconds"), cfg.int("source", "retries"), log)
    except SourceError as e:
        kept = f"Последний успешный отчёт сохранён: {out}" if Path(out).exists() else "Прежнего отчёта нет."
        log(f"ОШИБКА источника: {e}. Новый отчёт не создан. {kept}")
        return 1
    chain = make_chain(cfg, log)
    report = analyze(vacancies, chain, cfg, {"mode": "api", "url": url, **info}, log)
    _summary(report, write_report(report, out))
    if args.print:
        print_report(report)
    return 0


def cmd_check(args) -> int:
    """Проверочный режим: тот же анализатор на локальном файле. Лимит вакансий не применяется."""
    cfg = _config(args)
    try:
        vacancies = load_local(args.file)
    except SourceError as e:
        log(f"ОШИБКА: {e}")
        return 1
    out = args.out or str(Path(cfg.get("report", "dir")) / "check_report.json")
    chain = make_chain(cfg, log)
    if not chain and not args.no_ai:
        log("ВНИМАНИЕ: ни один движок ИИ недоступен, отчёт будет содержать только замечания, установленные кодом. "
            "Запустите `python -m vacancy_clarity doctor`.")
    report = analyze(vacancies, chain, cfg, {"mode": "file", "path": args.file}, log)
    _summary(report, write_report(report, out, history=False))
    if args.print:
        print_report(report)
    return 0


def _next_run(cfg, now: datetime.datetime) -> datetime.datetime:
    if cfg.get("schedule", "mode").strip().lower() == "daily":
        hour, minute = (int(x) for x in cfg.get("schedule", "daily_at").split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target if target > now else target + datetime.timedelta(days=1)
    return now + datetime.timedelta(minutes=cfg.int("schedule", "interval_minutes"))


def cmd_schedule(args) -> int:
    """Запускает рабочий режим по расписанию из настроек. Ошибка одного запуска не останавливает расписание."""
    cfg = _config(args)
    log(f"Расписание: {cfg.get('schedule', 'mode')} "
        f"(interval_minutes={cfg.get('schedule', 'interval_minutes')}, daily_at={cfg.get('schedule', 'daily_at')})")
    if not args.skip_first:
        cmd_run(args)
    while True:
        nxt = _next_run(cfg, datetime.datetime.now())
        log(f"Следующий запуск: {nxt.strftime('%Y-%m-%d %H:%M')}")
        while datetime.datetime.now() < nxt:
            time.sleep(min(30, max(1, (nxt - datetime.datetime.now()).total_seconds())))
        cmd_run(args)
        if args.once:
            return 0


def cmd_eval(args) -> int:
    evaluate.run(args.report, args.key, details=not args.brief)
    return 0


def cmd_doctor(args) -> int:
    """Показывает действующие настройки и проверяет движки ИИ одним вопросом."""
    cfg = _config(args)
    print(f"vacancy_clarity {__version__}; Python {sys.version.split()[0]}")
    print(f"Файл настроек: {cfg.path or 'не найден, действуют значения по умолчанию'}")
    print(f"Источник: {cfg.get('source', 'url')} (лимит {cfg.get('source', 'max_vacancies')})")
    print(f"Расписание: {cfg.get('schedule', 'mode')}")
    account, token = cfg.cloudflare_credentials
    print(f"Cloudflare: аккаунт {'задан' if account else 'не задан'}, токен {'задан' if token else 'не задан'}; "
          f"модель {cfg.get('ai', 'cloudflare_model')}")
    print(f"Ollama: {cfg.get('ai', 'ollama_url')}, модель {cfg.get('ai', 'ollama_model')}")
    if cfg.get("ai", "llm_base_url").strip():
        print(f"OpenAI-совместимый сервис: {cfg.get('ai', 'llm_base_url')}, модель {cfg.get('ai', 'llm_model')}, "
              f"ключ {'задан' if cfg.llm_api_key else 'не задан'}")
    chain = make_chain(cfg, lambda m: print(m))
    if not chain:
        print("ИТОГ: нет доступного движка ИИ. Запустите Ollama или задайте ключи Cloudflare в .env.")
        return 1
    from .questions import Question
    ok = 0
    for engine in chain.engines:
        engine.cache = None
        started = time.time()
        try:
            answer = engine.ask("Название должности: Дворник\nФрагменты текста вакансии:\n[1] (обязанности) Уборка двора.",
                                [Question("probe", "Названа ли в вакансии конкретная задача работника?",
                                          [("yes", "да"), ("no", "нет")])])
            print(f"{engine.name} ({engine.model}): отвечает, ответ на пробный вопрос «{answer['probe']}», "
                  f"{time.time() - started:.1f} с")
            ok += 1
        except Exception as e:
            print(f"{engine.name} ({engine.model}): НЕ РАБОТАЕТ — {e}")
    print("ИТОГ: " + ("ИИ готов к работе." if ok else "ни один движок не ответил."))
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vacancy_clarity", description="Отчёт о понятности условий работы в вакансиях.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--config", help="путь к config.ini (по умолчанию ./config.ini, если есть)")
        p.add_argument("--engine", help="движок ИИ: auto, cloudflare, openai, clef, clef-flash, ollama, none "
                                        "или цепочка через запятую")
        p.add_argument("--no-ai", action="store_true", help="не обращаться к ИИ (только замечания, установленные кодом)")
        p.add_argument("--dir", help="папка для отчётов")
        p.add_argument("--print", action="store_true", help="после записи вывести замечания в терминал")

    p = sub.add_parser("run", help="загрузить вакансии через API и построить отчёт")
    common(p)
    p.add_argument("--url", help="URL источника")
    p.add_argument("--max", type=int, help="максимальное число вакансий")
    p.add_argument("--out", help="путь к файлу отчёта")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("check", help="построить отчёт по локальному JSON в формате ответов API")
    common(p)
    p.add_argument("file", help="файл или папка с файлами *.json")
    p.add_argument("--out", help="путь к файлу отчёта")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("schedule", help="запускать рабочий режим по расписанию из настроек")
    common(p)
    p.add_argument("--url")
    p.add_argument("--max", type=int)
    p.add_argument("--out")
    p.add_argument("--skip-first", action="store_true", help="не запускать сразу, ждать первого срока")
    p.add_argument("--once", action="store_true", help="выйти после первого запуска по расписанию")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("eval", help="сверить отчёт с ключом ответов")
    p.add_argument("report")
    p.add_argument("key")
    p.add_argument("--brief", action="store_true", help="только итоговые числа")
    p.set_defaults(func=cmd_eval, config=None)

    p = sub.add_parser("doctor", help="проверить настройки и доступность ИИ")
    common(p)
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("Остановлено.")
        return 130
    except FileNotFoundError as e:
        log(f"ОШИБКА: {e}")
        return 1
