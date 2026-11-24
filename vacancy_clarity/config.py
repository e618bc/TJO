"""Настройки: значения по умолчанию, файл config.ini, переменные окружения и файл .env.

Порядок приоритета: аргументы командной строки, переменные окружения, config.ini, умолчания.
Ключи доступа читаются только из окружения или из .env и в отчёт не попадают.
"""
from __future__ import annotations

import configparser
import os
import re
from pathlib import Path

DEFAULTS = {
    "source": {
        "url": "http://opendata.trudvsem.ru/api/v1/vacancies/region/39",
        "page_size": "100",
        "max_vacancies": "200",
        "timeout_seconds": "150",
        "retries": "3",
    },
    "schedule": {
        "mode": "interval",          # interval — каждые N минут; daily — раз в сутки в заданное время
        "interval_minutes": "360",
        "daily_at": "06:00",
    },
    "ai": {
        "engine": "auto",            # auto, cloudflare, openai, clef, clef-flash, ollama, none или цепочка через запятую
        "cloudflare_model": "@cf/google/gemma-4-26b-a4b-it",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen3.5:4b",
        "timeout_seconds": "300",
        "retries": "2",
        "num_ctx": "4096",
        "ollama_threads": "0",       # 0 — половина ядер процессора
        "clef_threshold": "0.6",
        # Любой OpenAI-совместимый сервис: адрес вида https://host/v1, имя модели и, при необходимости,
        # дополнительные параметры запроса в виде JSON. Ключ — переменная LLM_API_KEY в .env.
        "llm_base_url": "",
        "llm_model": "",
        "llm_extra": "",     # минимальная вероятность варианта, чтобы ответ Clef считался уверенным
        "cache": ".cache/ai.sqlite",
    },
    "report": {
        "dir": "data",
        "hints": "true",             # добавлять ли необязательные наблюдения отдельным массивом
    },
    "policy": {
        "wide_range_ratio": "3.0",   # вилка зарплаты: максимум к минимуму, с которого она считается неинформативной
        "hint_range_ratio": "2.0",
        "pay_conflict": "0.25",      # расхождение суммы в тексте с вилкой, с которого это противоречие
        "pay_hint": "0.15",
        "pay_conflict_confirmed": "0.05",  # то же, если ИИ подтвердил расхождение
        "full_time_hours": "35",     # часов в неделю, с которых график соответствует полной занятости
        "min_monthly_pay": "5000",   # меньшая сумма не похожа на месячную зарплату
    },
}

# Переменные окружения, перекрывающие config.ini.
ENV_OVERRIDES = {
    "VC_SOURCE_URL": ("source", "url"),
    "VC_MAX_VACANCIES": ("source", "max_vacancies"),
    "VC_SOURCE_RETRIES": ("source", "retries"),
    "VC_SCHEDULE_MODE": ("schedule", "mode"),
    "VC_INTERVAL_MINUTES": ("schedule", "interval_minutes"),
    "VC_DAILY_AT": ("schedule", "daily_at"),
    "VC_AI_ENGINE": ("ai", "engine"),
    "VC_CLOUDFLARE_MODEL": ("ai", "cloudflare_model"),
    "VC_OLLAMA_URL": ("ai", "ollama_url"),
    "VC_OLLAMA_MODEL": ("ai", "ollama_model"),
    "LLM_BASE_URL": ("ai", "llm_base_url"),
    "LLM_MODEL": ("ai", "llm_model"),
    "LLM_EXTRA": ("ai", "llm_extra"),
    "VC_REPORT_DIR": ("report", "dir"),
}


def load_env_file(path: Path) -> None:
    """Читает пары КЛЮЧ=значение из .env, не перекрывая уже заданные переменные окружения."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            # «Голая» строка: 32 шестнадцатеричных символа — идентификатор аккаунта, иначе токен.
            name = "CLOUDFLARE_ACCOUNT_ID" if re.fullmatch(r"[0-9a-fA-F]{32}", line) else "CLOUDFLARE_API_TOKEN"
            os.environ.setdefault(name, line.strip("'\""))
            continue
        key, value = line.split("=", 1)
        key = re.sub(r"^export\s+", "", key.strip())
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


class Config:
    def __init__(self, path: str | None = None, overrides: dict | None = None):
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read_dict(DEFAULTS)
        self.path = None
        candidates = [path] if path else ["config.ini"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                self.parser.read(candidate, encoding="utf-8")
                self.path = candidate
        if path and not self.path:
            raise FileNotFoundError(f"Файл настроек не найден: {path}")
        load_env_file(Path(".env"))
        for env, (section, key) in ENV_OVERRIDES.items():
            if os.environ.get(env):
                self.parser.set(section, key, os.environ[env])
        for dotted, value in (overrides or {}).items():
            if value is not None:
                section, key = dotted.split(".", 1)
                self.parser.set(section, key, str(value))

    def get(self, section: str, key: str) -> str:
        return self.parser.get(section, key)

    def int(self, section: str, key: str) -> int:
        return int(float(self.parser.get(section, key)))

    def float(self, section: str, key: str) -> float:
        return float(self.parser.get(section, key))

    def bool(self, section: str, key: str) -> bool:
        return self.parser.get(section, key).strip().lower() in ("1", "true", "yes", "on", "да")

    @property
    def llm_api_key(self) -> str:
        """Ключ OpenAI-совместимого сервиса из окружения; пустая строка для серверов без ключа."""
        return os.environ.get("LLM_API_KEY", "").strip()

    @property
    def cloudflare_credentials(self):
        """Идентификатор аккаунта и токен Cloudflare из окружения; пустые строки, если их нет."""
        return (os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip(),
                os.environ.get("CLOUDFLARE_API_TOKEN", "").strip())
