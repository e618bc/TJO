"""Получение вакансий: API «Работа в России» и локальные файлы в формате его ответов."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class SourceError(Exception):
    """Источник недоступен или вернул ошибку."""


def _is_vacancy(obj) -> bool:
    return isinstance(obj, dict) and any(k in obj for k in ("job-name", "duty", "salary_min", "requirement"))


def extract_vacancies(data) -> list:
    """Достаёт вакансии из разных форм входа.

    Понимает ответ API ({"results": {"vacancies": [{"vacancy": {...}}]}}), список таких ответов,
    голый список вакансий, записи с обёрткой "vacancy" и без неё, одну вакансию.
    """
    if isinstance(data, list):
        out = []
        for item in data:
            out.extend(extract_vacancies(item))
        return out
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("vacancy"), dict):
        return [data["vacancy"]]
    results = data.get("results")
    if isinstance(results, dict) and "vacancies" in results:
        return extract_vacancies(results.get("vacancies") or [])
    if isinstance(results, list):
        return extract_vacancies(results)
    if isinstance(data.get("vacancies"), list):
        return extract_vacancies(data["vacancies"])
    return [data] if _is_vacancy(data) else []


def check_response(data, where: str) -> None:
    """Ответ-ошибка API приходит с кодом HTTP 200, но со status не 200 и без results."""
    if not isinstance(data, dict):
        return
    status = str(data.get("status", "200"))
    if status != "200" or ("results" not in data and "meta" in data and "error" in (data.get("meta") or {})):
        message = (data.get("meta") or {}).get("error", "") if isinstance(data.get("meta"), dict) else ""
        raise SourceError(f"{where}: источник вернул ошибку (status {status}) {message}".strip())


def load_local(path: str) -> list:
    """Читает файл или все *.json в папке. К порталу не обращается."""
    p = Path(path)
    if not p.exists():
        raise SourceError(f"Файл не найден: {path}")
    files = sorted(p.glob("*.json")) if p.is_dir() else [p]
    if not files:
        raise SourceError(f"В папке нет файлов *.json: {path}")
    vacancies = []
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as e:
            raise SourceError(f"{file}: не удалось прочитать JSON ({e})") from e
        check_response(data, str(file))
        vacancies.extend(extract_vacancies(data))
    return vacancies


def _page_url(base: str, page: int, limit: int) -> str:
    parts = urllib.parse.urlsplit(base)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"offset": str(page), "limit": str(limit)})   # в этом API offset — номер страницы
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch_api(url: str, max_vacancies: int, page_size: int, timeout: float, retries: int, log=print):
    """Загружает вакансии постранично. Сбой любой страницы — сбой всего запуска."""
    page_size = max(1, min(page_size, 100))
    vacancies, pages, total = [], 0, None
    while len(vacancies) < max_vacancies:
        page_url = _page_url(url, pages, page_size)
        data, error = None, None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(page_url, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8-sig"))
                break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                error = e
                if attempt < retries:
                    time.sleep(min(5 * (attempt + 1), 20))
        if data is None:
            raise SourceError(f"Страница {pages + 1}: источник недоступен ({type(error).__name__}: {error})")
        check_response(data, f"Страница {pages + 1}")
        batch = extract_vacancies(data)
        pages += 1
        if isinstance(data.get("meta"), dict):
            try:
                total = int(data["meta"].get("total"))
            except (TypeError, ValueError):
                pass
        log(f"Источник: страница {pages}, получено {len(batch)} вакансий")
        vacancies.extend(batch)
        if len(batch) < page_size or (total is not None and len(vacancies) >= total):
            break
    if not vacancies:
        # Пустой ответ на рабочем адресе почти всегда означает ошибку в URL (например, неверный код региона).
        # Чтобы не затереть последний успешный отчёт пустым, это считается ошибкой источника.
        raise SourceError("источник не вернул ни одной вакансии — проверьте URL")
    return vacancies[:max_vacancies], {"pages": pages, "total_in_source": total}


def dedup(vacancies: list):
    """Одна запись на ID: из повторов берётся вариант с более поздним date_modify.

    Возвращает (вакансии, пропущенные). Записи без ID в отчёт не попадают и перечисляются отдельно.
    """
    by_id, order, skipped = {}, [], []
    for index, v in enumerate(vacancies):
        vid = v.get("id") if isinstance(v, dict) else None
        if vid is None or str(vid).strip() == "":
            name = v.get("job-name", "") if isinstance(v, dict) else ""
            skipped.append({"position": index, "reason": "нет id", "job-name": str(name)[:80]})
            continue
        vid = str(vid)
        stamp = str(v.get("date_modify") or v.get("creation-date") or "")
        if vid not in by_id:
            by_id[vid] = (stamp, v)
            order.append(vid)
        elif stamp > by_id[vid][0]:
            by_id[vid] = (stamp, v)
    return [by_id[i][1] for i in order], skipped
