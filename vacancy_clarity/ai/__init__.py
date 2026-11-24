"""Шаг ИИ: движки отвечают на банк вопросов, выбирая варианты из закрытых списков."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request

from .. import BANK_VERSION
from ..questions import UNCLEAR, Question
from .cache import Cache
from .prompt import answer_schema, build_messages, parse_answers


class AIError(Exception):
    """Движок не смог ответить: сеть, квота, неверный ключ, модель недоступна."""


def post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise AIError(f"HTTP {e.code}: {body}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise AIError(f"{type(e).__name__}: {e}") from e


class Engine:
    """Общая часть движков: кэш, повторы, разбор ответа."""
    name = "base"

    def __init__(self, model: str, timeout: float, retries: int, cache: Cache | None):
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.cache = cache
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0

    def complete(self, messages: list, schema: dict) -> str:   # pragma: no cover - переопределяется
        raise NotImplementedError

    def ask(self, state: str, questions: list[Question]) -> dict:
        """Возвращает {идентификатор вопроса: вариант}; для неясных ответов — UNCLEAR."""
        if not questions:
            return {}
        messages = build_messages(state, questions)
        schema = answer_schema(questions)
        key = hashlib.sha256(json.dumps([self.name, self.model, BANK_VERSION, messages], ensure_ascii=False,
                                        sort_keys=True).encode("utf-8")).hexdigest()
        if self.cache:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                return parse_answers(cached, questions)
        last_error = None
        for _ in range(self.retries + 1):
            self.calls += 1
            try:
                text = self.complete(messages, schema)
                answers = parse_answers(text, questions)
                if all(v == UNCLEAR for v in answers.values()) and len(answers) > 2:
                    raise AIError("ответ модели не удалось разобрать")
                if self.cache:
                    self.cache.put(key, text)
                return answers
            except AIError as e:
                last_error = e
        self.failures += 1
        raise AIError(f"{self.name}: {last_error}")

    def describe(self) -> dict:
        return {"engine": self.name, "model": self.model, "calls": self.calls,
                "cache_hits": self.cache_hits, "failures": self.failures}


class OllamaEngine(Engine):
    """Локальная модель через Ollama. Ответ ограничен JSON-схемой с перечислением вариантов."""
    name = "ollama"

    def __init__(self, url: str, model: str, num_ctx: int, threads: int = 0, **kw):
        super().__init__(model, **kw)
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        # По умолчанию модель занимает половину ядер: на всех ядрах машина перестаёт отвечать.
        self.threads = threads or max(1, (os.cpu_count() or 2) // 2)

    def complete(self, messages: list, schema: dict) -> str:
        reply = post_json(f"{self.url}/api/chat", {
            "model": self.model, "messages": messages, "stream": False, "format": schema, "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0, "seed": 39, "num_ctx": self.num_ctx, "num_thread": self.threads},
        }, {}, self.timeout)
        content = (reply.get("message") or {}).get("content")
        if not content:
            raise AIError(f"пустой ответ: {str(reply)[:200]}")
        return content

    def available(self) -> str:
        """Пустая строка, если сервер и модель доступны; иначе причина."""
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=5) as r:
                names = [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return f"сервер Ollama не отвечает по адресу {self.url} ({type(e).__name__})"
        if not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return f"модель {self.model} не загружена; выполните: ollama pull {self.model}"
        return ""


class OpenAICompatEngine(Engine):
    """Любой сервис с OpenAI-совместимым адресом /chat/completions (облачный или свой сервер).

    Сначала ответ запрашивается по JSON-схеме; если сервис или модель её не поддерживает,
    выбирается более простой режим, вплоть до запроса только с моделью и сообщениями.
    """
    name = "openai"

    def __init__(self, base_url: str, token: str, model: str, extra: dict | None = None, **kw):
        super().__init__(model, **kw)
        url = base_url.rstrip("/")
        self.url = url if url.endswith("/chat/completions") else url + "/chat/completions"
        self.token = token
        self.extra = dict(extra or {})
        self.formats = ["json_schema", "json_object", "plain", "minimal"]

    def complete(self, messages: list, schema: dict) -> str:
        mode = self.formats[0]
        if mode == "minimal":
            payload = {"model": self.model, "messages": messages}
        else:
            payload = {"model": self.model, "messages": messages, "temperature": 0, "seed": 39,
                       "max_tokens": 600, **self.extra}
        if mode == "json_schema":
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "answers", "schema": schema}}
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            reply = post_json(self.url, payload, headers, self.timeout)
        except AIError as e:
            # Сервис не принял параметры запроса: пробуем более простой режим вывода.
            if len(self.formats) > 1 and ("HTTP 400" in str(e) or "HTTP 422" in str(e) or "JSON Mode" in str(e)):
                self.formats.pop(0)
                return self.complete(messages, schema)
            raise
        choices = reply.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        content = message.get("content") or reply.get("response")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        if not content:
            raise AIError(f"пустой ответ: {str(reply)[:200]}")
        return content

    def available(self) -> str:
        return "" if self.url.startswith("http") and self.model else "не заданы адрес сервиса и модель (llm_base_url, llm_model)"


class CloudflareEngine(OpenAICompatEngine):
    """Workers AI через OpenAI-совместимый адрес. Этап рассуждений модели выключен:
    вопросы с закрытыми вариантами его не требуют, а с ним ответ в десять раз дольше и дороже."""
    name = "cloudflare"

    def __init__(self, account_id: str, token: str, model: str, **kw):
        super().__init__(f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", token, model,
                         extra={"chat_template_kwargs": {"enable_thinking": False}}, **kw)

    def available(self) -> str:
        return "" if self.token else "не заданы CLOUDFLARE_ACCOUNT_ID и CLOUDFLARE_API_TOKEN"


class ClefEngine(Engine):
    """Модель решений Cloudflare Clef: на каждый вопрос возвращает вероятности вариантов, текста не пишет.

    Вариант принимается, если его вероятность не ниже порога; иначе ответ считается неясным.
    """
    name = "clef"

    def __init__(self, account_id: str, token: str, model: str, threshold: float = 0.6, **kw):
        super().__init__(model, **kw)
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/{model}"
        self.token = token
        self.threshold = threshold
        self.input_tokens = 0

    def ask(self, state: str, questions: list[Question]) -> dict:
        if not questions:
            return {}
        payload = {"model": self.model, "state": state, "questions": {
            q.id: {"type": "choice", "instructions": q.text,
                   "criteria": {**{oid: desc for oid, desc in q.options}, UNCLEAR: "по вакансии нельзя определить"}}
            for q in questions}}
        key = hashlib.sha256(json.dumps([self.name, self.model, BANK_VERSION, payload], ensure_ascii=False,
                                        sort_keys=True).encode("utf-8")).hexdigest()
        raw = self.cache.get(key) if self.cache else None
        if raw is not None:
            self.cache_hits += 1
            reply = json.loads(raw)
        else:
            last_error = None
            for _ in range(self.retries + 1):
                self.calls += 1
                try:
                    reply = post_json(self.url, payload, {"Authorization": f"Bearer {self.token}"}, self.timeout)
                    reply = reply.get("result", reply)
                    if not isinstance(reply.get("answers"), dict):
                        raise AIError(f"нет ответов: {str(reply)[:200]}")
                    break
                except AIError as e:
                    last_error = e
            else:
                self.failures += 1
                raise AIError(f"{self.name}: {last_error}")
            self.input_tokens += (reply.get("usage") or {}).get("input_tokens", 0)
            if self.cache:
                self.cache.put(key, json.dumps(reply, ensure_ascii=False))
        answers = {}
        for q in questions:
            a = reply["answers"].get(q.id) or {}
            choice = a.get("choice")
            p = (a.get("probabilities") or {}).get(choice, 0) if choice else 0
            answers[q.id] = choice if choice in q.option_ids and p >= self.threshold else UNCLEAR
        return answers

    def describe(self) -> dict:
        return dict(super().describe(), threshold=self.threshold, input_tokens=self.input_tokens)

    def available(self) -> str:
        return "" if self.token else "не заданы CLOUDFLARE_ACCOUNT_ID и CLOUDFLARE_API_TOKEN"


class EngineChain:
    """Несколько движков по порядку: при сбое первого вопрос уходит следующему."""

    def __init__(self, engines: list):
        self.engines = engines
        self.disabled: dict = {}

    def __bool__(self) -> bool:
        return bool(self.engines)

    def ask(self, state: str, questions: list[Question]):
        """Возвращает (ответы, имя движка). Бросает AIError, если не ответил никто."""
        errors = []
        for engine in self.engines:
            if engine.name in self.disabled:
                continue
            try:
                return engine.ask(state, questions), engine.name
            except AIError as e:
                errors.append(str(e))
                # Постоянные ошибки (ключ, квота, нет модели) отключают движок до конца запуска.
                if any(code in str(e) for code in ("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 429", "URLError")):
                    self.disabled[engine.name] = str(e)
        raise AIError("; ".join(errors) or "нет доступных движков")

    def describe(self) -> list:
        return [dict(e.describe(), disabled=self.disabled.get(e.name, "")) for e in self.engines]


def make_chain(cfg, log=print) -> EngineChain:
    """Собирает цепочку движков по настройке ai.engine."""
    spec = cfg.get("ai", "engine").strip().lower()
    if spec in ("none", "off", ""):
        return EngineChain([])
    if spec == "auto":
        names = ["cloudflare"] + (["openai"] if cfg.get("ai", "llm_base_url").strip() else []) + ["ollama"]
    else:
        names = [x.strip() for x in spec.split(",") if x.strip()]
    cache_path = cfg.get("ai", "cache").strip()
    cache = Cache(cache_path) if cache_path else None
    common = dict(timeout=cfg.float("ai", "timeout_seconds"), retries=cfg.int("ai", "retries"), cache=cache)
    engines = []
    for name in names:
        if name == "cloudflare":
            account, token = cfg.cloudflare_credentials
            engine = CloudflareEngine(account, token, cfg.get("ai", "cloudflare_model"), **common)
            reason = "" if account and token else engine.available()
        elif name == "openai":
            raw_extra = cfg.get("ai", "llm_extra").strip()
            try:
                extra = json.loads(raw_extra) if raw_extra else {}
            except ValueError as e:
                raise ValueError(f"ai.llm_extra должен быть JSON-объектом: {e}") from e
            engine = OpenAICompatEngine(cfg.get("ai", "llm_base_url").strip(), cfg.llm_api_key,
                                        cfg.get("ai", "llm_model").strip(), extra, **common)
            reason = engine.available()
        elif name in ("clef", "clef-flash"):
            account, token = cfg.cloudflare_credentials
            engine = ClefEngine(account, token, name, cfg.float("ai", "clef_threshold"), **common)
            reason = "" if account and token else engine.available()
        elif name == "ollama":
            engine = OllamaEngine(cfg.get("ai", "ollama_url"), cfg.get("ai", "ollama_model"),
                                  cfg.int("ai", "num_ctx"), cfg.int("ai", "ollama_threads"), **common)
            reason = engine.available()
        else:
            raise ValueError(f"Неизвестный движок ИИ: {name}")
        if reason:
            log(f"ИИ: движок {name} пропущен — {reason}")
        else:
            engines.append(engine)
    return EngineChain(engines)
