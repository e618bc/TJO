"""Таблица решений: из фактов (код) и ответов на вопросы (ИИ) получаются замечания.

Принципы:
- пропуск выносится, только если код ничего не нашёл и ИИ это подтвердил;
- противоречие выносится, только если значения несовместимы по коду и ИИ согласен;
- неясный ответ ИИ замечания не создаёт;
- текст замечаний собирается из шаблонов, модель его не пишет.

Замечания (`issues`) — то, что редактору нужно уточнить у работодателя.
Подсказки (`hints`) — спорные наблюдения, которые можно уточнить, но не обязательно.
"""
from __future__ import annotations

import re

from . import facts as F
from .questions import UNCLEAR
from .textnorm import norm

TOPIC_TITLES = {
    "title": "Название должности", "duties": "Обязанности", "requirements": "Требования", "salary": "Оплата",
    "schedule": "График работы", "location": "Место работы", "employment": "Тип занятости",
}
TYPE_TITLES = {"missing": "Пропуск", "ambiguity": "Неоднозначность", "contradiction": "Противоречие"}

# Формулировки, недопустимые в тексте проблемы и рекомендации: выводы о законе и оценки работодателя.
FORBIDDEN = [r"нарушен", r"незаконн", r"противозаконн", r"по закону", r"закон\w* (запрещ|обязыва|требу)",
             r"\bТК\b", r"Трудов\w+ кодекс", r"\bст\.\s*\d+", r"\bМРОТ\b", r"дискриминац", r"штраф",
             r"\bобязан[аоы]?\b", r"недопустим", r"занижен", r"ниже рынка", r"скорее всего", r"вероятно"]

_STREET = re.compile(r"\d|\bул\b|улиц|проспект|\bпр-?т?\b|переул|\bпер\b|площад|шоссе|набережн|бульвар|проезд|"
                     r"микрорайон|\bмкр\b|\bв/ч\b|школ|больниц|сад\b", re.I)
_DAY_LIKE = ("full", "day5", "irregular")


def short(text: str, limit: int = 90) -> str:
    """Короткий фрагмент цитаты для текста проблемы."""
    text = " ".join(str(text).split()).rstrip(" .;,")
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit] + "…"


def years(n: int) -> str:
    return f"{n} " + ("год" if n % 10 == 1 and n % 100 != 11 else "года" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "лет")


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def make(rule, topic, kind, field, quote, problem, recommendation, field_2=None, quote_2=None, evidence=None):
    issue = {"topic": topic, "topic_title": TOPIC_TITLES[topic], "type": kind, "type_title": TYPE_TITLES[kind],
             "rule": rule, "field": field, "quote": quote if kind != "missing" else ""}
    if kind == "contradiction":
        issue["field_2"] = field_2 or field
        issue["quote_2"] = quote_2 or ""
    issue["problem"] = problem
    issue["recommendation"] = recommendation
    if evidence:
        issue["evidence"] = evidence
    return issue


class Decision:
    def __init__(self, f: F.Facts, answers, policy: dict):
        self.f = f
        self.ai = answers is not None
        self.answers = answers or {}
        self.p = policy
        self.issues: list = []
        self.hints: list = []

    def a(self, qid: str):
        """Ответ ИИ на вопрос или None, если вопрос не задавался либо ответ неясен."""
        value = self.answers.get(qid)
        return None if value in (None, UNCLEAR) else value

    def ev(self, *qids) -> dict:
        return {q: self.answers[q] for q in qids if q in self.answers}

    def issue(self, *args, **kw) -> None:
        self.issues.append(make(*args, **kw))

    def hint(self, rule: str, topic: str, field: str, quote: str, note: str, question: str) -> None:
        self.hints.append({"topic": topic, "topic_title": TOPIC_TITLES[topic], "rule": rule, "field": field,
                           "quote": quote, "note": note, "question": question})

    def issues_for(self, topic: str) -> list:
        return [it for it in self.issues if it["topic"] == topic]

    def first_sentence(self, *fields):
        for name in fields:
            found = self.f.field_sentences(name)
            if found:
                return found[0]
        return self.f.sentences[0] if self.f.sentences else None

    def refs_to(self, topic: str) -> list:
        """Предложения-отсылки («на собеседовании»), которые ИИ отнёс к теме."""
        out = []
        for i, c in enumerate(self.f.of("reference")[:2], 1):
            if self.a(f"ref_{i}") == topic:
                out.append(c.sent)
        return out

    def run(self):
        for step in (self.duties, self.title, self.requirements, self.salary, self.schedule, self.employment,
                     self.location):
            step()
        return self.finish()

    def duties(self):
        f = self.f
        duty = F.get(f.vacancy, "duty", "")
        skills = F.get(f.vacancy, "skills", [])
        has_skills = isinstance(skills, list) and any(isinstance(s, str) and not F.is_filler(s) for s in skills)
        kind = self.a("duties")
        sent = self.first_sentence("duty")
        if F.is_filler(duty) and not has_skills and kind != "concrete":
            self.issue("DUT-3", "duties", "missing", "duty", "",
                       "Обязанности не описаны: поле не содержит текста.",
                       "Попросите работодателя перечислить основные задачи работника.", evidence=self.ev("duties"))
            return
        if not self.ai and isinstance(duty, str) and len(duty) < 70 and not has_skills:
            low = norm(duty).lower()        # без ИИ отмечаются только очевидные заглушки
            kind = "ref" if F.DUTY_REFERENCE.search(low) else "boss" if re.search(r"^\W*выполнение\s+(\w+\s+)?поручени", low) else None
        if kind in (None, "concrete") or sent is None:
            return
        if kind in ("title", "general", "promo", "traits") and isinstance(duty, str) and \
                (F.DUTY_SECTION.search(norm(duty)) or len(duty) > 400):
            return                          # в длинном тексте с разделом обязанностей задачи есть
        texts = {
            "ref": ("DUT-1", "Вместо перечня обязанностей дана отсылка к должностной инструкции или договору.",
                    "Попросите перечислить основные задачи, которые работник выполняет ежедневно."),
            "title": ("DUT-1", "Обязанности повторяют название должности и не называют ни одной задачи.",
                      "Попросите указать, что именно делает работник и за что он отвечает."),
            "general": ("DUT-1", "Обязанности описаны общими словами, конкретные задачи не названы.",
                        "Попросите перечислить конкретные задачи: что нужно делать и с чем работать."),
            "boss": ("DUT-4", "Обязанности описаны только как поручения руководителя, конкретные задачи не названы.",
                     "Попросите перечислить типовые задачи работника."),
            "traits": ("DUT-2", "В обязанностях указаны качества или требования к кандидату, а не то, что нужно делать.",
                       "Попросите описать задачи работника; требования к кандидату перенести в соответствующее поле."),
            "promo": ("DUT-2", "В описании только сведения о работодателе, льготах или условиях, задачи работника не названы.",
                      "Попросите перечислить, что именно делает работник на этой должности."),
        }
        rule, problem, rec = texts[kind]
        self.issue(rule, "duties", "ambiguity", sent.field, sent.quote(), problem, rec, evidence=self.ev("duties"))

    def title(self):
        f = self.f
        if not f.title.strip():
            return
        kind, duties, match, cover = self.a("title_kind"), self.a("duties"), self.a("title_match"), self.a("title_cover")
        if kind == "generic" and duties not in (None, "concrete"):
            self.issue("TTL-1", "title", "ambiguity", "job-name", f.title,
                       "По названию нельзя понять, какая работа предлагается, а обязанности его не раскрывают.",
                       "Уточните у работодателя наименование должности или профессии с направлением работы.",
                       evidence=self.ev("title_kind", "duties"))
        elif kind == "generic" and duties == "concrete":
            self.hint("TTL-1", "title", "job-name", f.title, "Название общее, но работа понятна из обязанностей.",
                      "Можно уточнить наименование должности с направлением работы.")
        if kind == "multi" and cover == "part":
            self.issue("TTL-3", "title", "ambiguity", "job-name", f.title,
                       "В названии несколько разных должностей, а обязанности описаны не для всех.",
                       "Уточните, одна это должность с совмещением или несколько отдельных вакансий.",
                       evidence=self.ev("title_kind", "title_cover"))
        sent = self.first_sentence("duty")
        if duties == "concrete" and match == "diff" and sent is not None:
            self.issue("TTL-2", "title", "contradiction", "job-name", f.title,
                       f"Должность называется «{short(f.title, 60)}», а обязанности описывают другую работу: "
                       f"«{short(sent.raw)}».",
                       "Уточните, на какую должность открыта вакансия, и приведите название и обязанности в соответствие.",
                       field_2=sent.field, quote_2=sent.quote(), evidence=self.ev("duties", "title_match"))

    def requirements(self):
        f = self.f
        if f.experience_present and ((f.qualification_flag == "with" and f.experience == 0)
                                     or (f.qualification_flag == "without" and f.experience >= 1)):
            self.issue("REQ-4", "requirements", "contradiction", "qualification", str(F.get(f.vacancy, "qualification")),
                       f"В поле квалификации указано «{F.get(f.vacancy, 'qualification')}», а в поле опыта — {f.experience}.",
                       "Уточните, требуется ли опыт работы и какой.",
                       field_2="requirement.experience", quote_2=str(f.experience))

        exp_text = self.a("exp_text")
        required = ("req_nodur", "lt1", "y1_2", "y3plus")
        # Код сравнивает числа; ИИ может снять замечание, если фраза не о требовании к опыту работы.
        not_a_demand = exp_text in ("none", "wish", "other", "notreq")
        if f.experience_present and self.ai:
            for c in f.of("exp"):
                text = c.sent.text
                hit = None
                if F.EXP_NOT_CONSIDERED.search(text):
                    hit = f.experience == 0 and not not_a_demand
                elif F.EXP_NOT_REQUIRED.search(text):
                    hit = f.experience >= 1 and exp_text not in required and exp_text not in ("wish", "other", "none")
                elif c.value and not F.EXP_SOFT.search(text):
                    lo, hi = c.value
                    if f.experience == 0:
                        hit = lo >= 1 and not not_a_demand
                    elif hi < 1:
                        hit = f.experience >= 2 and not not_a_demand
                    else:
                        hit = not (lo <= f.experience <= hi) and abs(f.experience - lo) >= 1 and not not_a_demand
                if hit:
                    self.issue("REQ-2", "requirements", "contradiction", "requirement.experience", str(f.experience),
                               f"В поле опыта указано {years(f.experience)}, а в тексте: «{short(c.sent.raw)}».",
                               "Уточните, какой опыт работы обязателен, и приведите поле опыта и текст к одному значению.",
                               field_2=c.sent.field, quote_2=c.sent.quote(), evidence=self.ev("exp_text"))
                    break
            else:
                if f.experience == 0 and exp_text == "req_nodur" and f.of("exp"):
                    s = f.of("exp")[0].sent
                    self.hint("REQ-2", "requirements", s.field, s.quote(),
                              "В поле опыта указано 0, а в тексте опыт назван без срока.",
                              "Можно уточнить, обязателен ли опыт работы и какой.")

        edu_text = self.a("edu_text")
        # Требования к образованию ищутся сначала в полях требований; фразы о предмете работы
        # («программы основного общего образования») требованиями не считаются.
        order = {"requirements": 0, "requirement.qualification": 1, "qualification": 2, "duty": 9}
        edu_sents = sorted((s for s in f.sentences if re.search(r"образовани", s.text, re.I)
                            and any(rx.search(s.text) for _, rx in F.EDU_LEVELS)
                            and not re.search(r"программ\w*\s+(\w+\s+){0,3}образовани|образовательн\w+\s+программ|"
                                              r"(учащ|обучающ|школьник|дет)\w*\s+(\w+\s+){0,3}образовани|ФГОС|стандарт\w*\s+(\w+\s+){0,4}образовани",
                                              s.text, re.I)),
                           key=lambda s: order.get(s.field, 5))
        text_levels = [lvl for s in edu_sents[:1] for lvl, rx in F.EDU_LEVELS if rx.search(s.text)]
        if edu_sents and F.EDU_BARE_MIDDLE.search(edu_sents[0].text):
            text_levels = []                  # «высшее или среднее»: минимальный уровень не определить
        text_min = min(text_levels, key=F.EDU_RANK.get) if text_levels else None
        # Два ключа: уровень, найденный кодом, и ответ ИИ должны совпасть («или» — минимальный из названных).
        if edu_text in ("school", "spo", "high"):
            text_min = edu_text if text_min in (None, edu_text) else None
        elif edu_text != "or":
            text_min = None
        if f.education in ("notreq", "school", "spo", "high") and text_min and text_min != f.education:
            sent = edu_sents[0] if edu_sents else None
            if sent is not None:
                self.issue("REQ-3", "requirements", "contradiction", "requirement.education", f.education_raw,
                           f"В поле образования указано «{short(f.education_raw, 50)}», а в тексте: «{short(sent.raw)}».",
                           "Уточните минимально необходимый уровень образования и исправьте поле или текст.",
                           field_2=sent.field, quote_2=sent.quote(), evidence=self.ev("edu_text"))
        for i, c in enumerate(f.of("req_pair")[:2], 1):
            if self.a(f"req_pair_{i}") == "conflict":
                self.issue("REQ-7", "requirements", "contradiction", c.sent.field, c.sent.quote(),
                           "В разных местах вакансии одно и то же требование к кандидату описано противоположно.",
                           "Уточните, обязательно ли это требование, и оставьте в вакансии одно значение.",
                           field_2=c.value.field, quote_2=c.value.quote(), evidence=self.ev(f"req_pair_{i}"))
                break

        # Образование «Не указано» без упоминаний в тексте — фон, замечания нет.
        # Пропуск — только если о требованиях нет ничего: ни текста, ни образования, ни квалификации, ни опыта.
        req = F.get(f.vacancy, "requirement", {})
        req_text = F.get(f.vacancy, "requirements", "")
        nothing = (F.is_filler(req_text) and f.education == "empty" and not f.experience
                   and not (isinstance(req, dict) and not F.is_filler(req.get("qualification")))
                   and F.is_filler(F.get(f.vacancy, "qualification", "")) and not f.of("exp")
                   and not re.search(r"требовани|образовани|опыт|навык|умени|знани", f.free_text, re.I))
        if nothing:
            self.issue("REQ-1", "requirements", "missing", "requirements", "",
                       "Требования к кандидату не указаны: нет ни образования, ни опыта, ни навыков.",
                       "Уточните требуемое образование, опыт и обязательные навыки или документы; "
                       "если требований нет, попросите указать это явно.")

    def salary(self):
        f, p = self.f, self.p
        lo, hi = f.salary_min, f.salary_max
        pay = [c for c in f.of("pay") if c.value >= p["min_monthly_pay"]]
        if hi and lo > hi:
            self.issue("PAY-3", "salary", "contradiction", "salary_min", str(lo),
                       f"Минимальная зарплата ({money(lo)}) больше максимальной ({money(hi)}).",
                       "Уточните нижнюю и верхнюю границы оплаты.", field_2="salary_max", quote_2=str(hi))
            return
        if 0 < lo < p["min_monthly_pay"]:
            self.issue("PAY-5", "salary", "ambiguity", "salary_min", str(lo),
                       f"Сумма {money(lo)} не похожа на месячную зарплату, единица оплаты не указана.",
                       "Уточните, за какой период или объём работы указана сумма и каков заработок за месяц.")
            return
        if self.a("pay_texts") == "conflict" and not self.issues_for("salary"):
            by_value = {}
            for c in pay:
                if c.info["bound"] != "to":
                    by_value.setdefault(c.value, c)
            vals = sorted(by_value)
            a, b = by_value[vals[0]], by_value[vals[-1]]
            if a.sent.n != b.sent.n:
                self.issue("PAY-2", "salary", "contradiction", a.sent.field, a.sent.quote(),
                           f"В разных местах вакансии названы разные суммы оплаты: {money(a.value)} и {money(b.value)}.",
                           "Уточните фактический размер оплаты и оставьте в вакансии одно значение.",
                           field_2=b.sent.field, quote_2=b.sent.quote(), evidence=self.ev("pay_texts"))
                return
        if lo == 0 and hi == 0:
            if pay or self.a("pay_named") == "yes":
                return
            if self.ai and f.sentences and self.a("pay_named") != "no":
                return                                   # ИИ не подтвердил пропуск
            ref = (self.refs_to("pay") or [None])[0]
            if ref is None:
                ref = next((s for s in f.sentences if F.PAY_WORD.search(s.text)
                            and re.search(r"зарплат|заработн|оклад|доход|оплат\w+\s+труд", s.text, re.I)), None)
            if ref is not None:
                self.issue("PAY-1", "salary", "ambiguity", ref.field, ref.quote(),
                           "Размер оплаты не указан: поля зарплаты пустые, в тексте суммы нет.",
                           "Уточните оклад или диапазон заработной платы за месяц.", evidence=self.ev("pay_named"))
            else:
                self.issue("PAY-1", "salary", "missing", "salary_min", "",
                           "Размер оплаты не указан ни в полях, ни в тексте.",
                           "Уточните оклад или диапазон заработной платы за месяц.", evidence=self.ev("pay_named"))
            return

        top = hi if hi else None
        confirmed = self.a("pay_consistency") == "conflict"
        tol = p["pay_conflict_confirmed"] if confirmed else p["pay_conflict"]

        def outside(c) -> bool:
            v, bound = c.value, c.info["bound"]
            if bound == "from":
                return v > (top or lo) * (1 + tol) or v < lo * (1 - tol)
            if bound == "to":
                return (top is not None and v > top * (1 + tol)) or v < lo * (1 - tol)
            return v < lo * (1 - tol) or (top is not None and v > top * (1 + tol))

        def net_ok(c) -> bool:
            return bool(F.NET_PAY.search(c.sent.text)) and lo * 0.8 <= c.value <= (top or lo)

        out = [c for c in pay if outside(c) and not net_ok(c)]
        if out and len(out) == len(pay) and confirmed:
            c = out[0]
            above = c.value > (top or lo)
            fld, val = ("salary_max", top) if above and top else ("salary_min", lo)
            self.issue("PAY-2", "salary", "contradiction", fld, str(val),
                       f"В полях указана зарплата {('от ' + money(lo)) if not top else money(lo) + '–' + money(top)}, "
                       f"а в тексте названа сумма {money(c.value)}.",
                       "Уточните фактический размер оплаты и исправьте поля или текст.",
                       field_2=c.sent.field, quote_2=c.sent.quote(), evidence=self.ev("pay_consistency"))

        basis = self.a("pay_basis")
        if basis == "rate_missing":
            s = f.of("unit")[0].sent
            self.issue("PAY-5", "salary", "ambiguity", s.field, s.quote(),
                       "Оплата почасовая, сменная, сдельная или процентная, но ставка или расценка не названа, "
                       "и непонятно, как сумма в полях соотносится с заработком.",
                       "Уточните ставку или расценку, порядок расчёта и гарантированный минимум за месяц.",
                       evidence=self.ev("pay_basis"))
            return
        text_ceiling = max((c for c in pay if c.info["bound"] == "to"), key=lambda c: c.value, default=None)
        if text_ceiling is not None and text_ceiling.value >= 1.5 * (hi or lo) and not self.issues_for("salary") \
                and self.a("pay_consistency") != "notpay":
            s = text_ceiling.sent
            self.issue("PAY-4", "salary", "ambiguity", s.field, s.quote(),
                       f"В полях указана зарплата {('от ' + money(lo)) if not hi else money(lo) + '–' + money(hi)}, "
                       f"а в тексте — доход до {money(text_ceiling.value)}; не ясно, что гарантировано и от чего "
                       "зависит остальное.",
                       "Уточните гарантированную часть оплаты и условия, при которых доход выше.",
                       evidence=self.ev("pay_consistency", "pay_explained"))
            return
        ratio = hi / lo if lo and hi else 0
        explained = self.a("pay_explained")
        if ratio >= p["wide_range_ratio"] and (explained == "no" or not f.sentences):
            self.issue("PAY-4", "salary", "ambiguity", "salary_max", str(hi),
                       f"Диапазон от {money(lo)} до {money(hi)} не позволяет понять реальный уровень оплаты.",
                       "Уточните оклад и от чего зависит остальная часть заработка.", evidence=self.ev("pay_explained"))
        elif ratio >= p["hint_range_ratio"] and explained == "no":
            self.hint("PAY-4", "salary", "salary_max", str(hi), f"Диапазон оплаты широкий: от {money(lo)} до {money(hi)}.",
                      "Можно уточнить, от чего зависит размер заработка.")
        if lo and not hi and self.refs_to("pay"):
            s = self.refs_to("pay")[0]
            self.hint("PAY-7", "salary", s.field, s.quote(), "Указана только нижняя граница оплаты, остальное отложено.",
                      "Можно уточнить верхнюю границу или состав оплаты.")
        if lo and self.a("volume_text") == "part":
            self.hint("PAY-6", "salary", "salary_min", str(lo), "Работа неполная, но не сказано, за какой объём указана сумма.",
                      "Можно уточнить, за какую долю ставки или сколько часов указана оплата.")

    def schedule(self):
        f = self.f
        fs = f.schedule
        regime = fs.regime
        cands = f.of("schedule")
        text_days = any(c.info.has_days for c in cands)
        text_hours = any(c.info.has_hours for c in cands)
        consistency = self.a("sch_consistency")

        # Противоречия: поле против текста и текст против текста.
        conflict = None
        for c in cands:
            cr = c.info.regime
            if regime in _DAY_LIKE and cr in ("shift", "rota"):
                conflict = ("SCH-2", c)
            elif (regime in _DAY_LIKE or fs.day_only) and c.info.night and not c.info.day_only \
                    and not re.search(r"без\s+ночн|кроме\s+ночн|не\s+ночн", c.sent.text, re.I):
                conflict = ("SCH-3" if fs.day_only else "SCH-2", c)
            elif fs.night and not fs.day_only and c.info.day_only:
                conflict = ("SCH-3", c)
            if conflict:
                break
        if conflict and consistency in ("conflict", "one"):
            rule, c = conflict
            in_title = c.sent.field == "job-name"
            field_value = f.schedule_raw or ", ".join(f.shift_values)
            self.issue("TTL-4" if in_title else rule, "schedule", "contradiction", "schedule", field_value,
                       f"В поле графика указано «{short(field_value, 60)}», а в другом месте вакансии: «{short(c.sent.raw)}».",
                       "Уточните фактический режим работы и исправьте поле графика или текст.",
                       field_2=c.sent.field, quote_2=c.sent.quote(), evidence=self.ev("sch_consistency"))
        elif consistency == "conflict" and fs.has_params and any(
                (c.info.signature() - fs.signature()) or (c.info.weekend_work and not fs.weekend_work)
                for c in cands if c.sent.field != "schedule"):
            c = next(c for c in cands if c.sent.field != "schedule"
                     and ((c.info.signature() - fs.signature()) or (c.info.weekend_work and not fs.weekend_work)))
            self.issue("SCH-2", "schedule", "contradiction", "schedule", f.schedule_raw,
                       f"В поле графика указано «{short(f.schedule_raw, 60)}», а в другом месте вакансии: «{short(c.sent.raw)}».",
                       "Уточните фактический режим работы и исправьте поле графика или текст.",
                       field_2=c.sent.field, quote_2=c.sent.quote(), evidence=self.ev("sch_consistency"))
        elif consistency == "conflict":
            ratio_sets = [(c, set(c.info.ratios)) for c in cands if c.info.ratios]
            pair = next(((a, b) for i, (a, ra) in enumerate(ratio_sets) for b, rb in ratio_sets[i + 1:]
                         if not ra & rb), None)
            if pair:
                a, b = pair
                self.issue("SCH-4", "schedule", "contradiction", a.sent.field, a.sent.quote(),
                           "В вакансии названы два разных графика, и не объяснено, какой действует.",
                           "Уточните, какой из графиков относится к этой должности.",
                           field_2=b.sent.field, quote_2=b.sent.quote(), evidence=self.ev("sch_consistency"))
            elif cands:
                s = cands[0].sent
                self.hint("SCH-5", "schedule", s.field, s.quote(), "Сведения о графике в разных местах вакансии расходятся.",
                          "Можно уточнить, какой режим работы действует и по каким правилам.")
        elif consistency == "options" and cands:
            s = cands[0].sent
            self.hint("SCH-5", "schedule", s.field, s.quote(), "График дан вариантами.",
                      "Можно уточнить, какой вариант предлагается и с какими часами.")

        if self.a("extra_time") == "not_described":
            s = f.of("extra")[0].sent
            self.issue("SCH-7", "schedule", "ambiguity", s.field, s.quote(),
                       "Упомянута работа вне основного графика, но не сказано, как часто это бывает и на каких условиях.",
                       "Уточните, как часто бывает работа вне графика, как она оплачивается или компенсируется.",
                       evidence=self.ev("extra_time"))

        for s in [x for x in f.sentences if x.field == "schedule"] + f.sentences:
            check = F.weekly_check(s.text)
            if check and abs(check[0] - check[1]) >= max(3, 0.1 * check[0]):
                self.issue("SCH-8", "schedule", "contradiction", s.field, s.quote(),
                           f"Часы работы в день дают около {check[1]:g} ч в неделю, а заявлено {check[0]} ч.",
                           "Уточните время начала и окончания работы, перерыв и продолжительность рабочей недели.")
                break

        # Неполнота: открытый режим без дней и часов.
        open_regime = regime in ("flex", "shift", "part", "rota") or (not regime and not fs.has_params)
        if not open_regime or fs.has_params or text_days or text_hours:
            if regime in ("flex", "shift", "part", "rota") and (fs.has_days or text_days) != (fs.has_hours or text_hours):
                self.hint("SCH-5", "schedule", "schedule", f.schedule_raw, "Параметры графика названы частично.",
                          "Можно уточнить недостающее: дни и чередование или часы работы.")
            elif regime in ("full", "") and not fs.has_hours and not text_hours and f.schedule_raw:
                self.hint("SCH-6", "schedule", "schedule", f.schedule_raw, "Часы работы не названы.",
                          "Можно уточнить время начала и окончания работы.")
            return
        days, hours = self.a("days_named"), self.a("hours_named")
        confirmed = (days == "no" and hours == "no") or not f.sentences
        if not confirmed:
            if days != hours and "no" in (days, hours):
                self.hint("SCH-5", "schedule", "schedule", f.schedule_raw, "Параметры графика названы частично.",
                          "Можно уточнить недостающее: дни и чередование или часы работы.")
            return
        ref = (self.refs_to("schedule") or [None])[0]
        what = {"flex": "гибкий график", "shift": "сменная работа", "part": "неполный рабочий день",
                "rota": "вахтовый метод"}.get(regime, "")
        ask = {"rota": "длительность вахты и межвахтового отдыха, продолжительность смены",
               "shift": "длительность смены и время её начала, чередование рабочих и выходных дней"
                        + ("" if fs.day_only else ", наличие ночных смен"),
               "part": "количество рабочих часов в день или неделю и дни работы"}.get(
            regime, "рабочие дни, время начала и окончания работы")
        if f.schedule_raw.strip():
            self.issue("SCH-1", "schedule", "ambiguity", "schedule", f.schedule_raw,
                       f"Указан режим «{short(f.schedule_raw, 50)}», но нигде нет дней и часов работы." if what
                       else "По полю графика нельзя понять дни и часы работы.",
                       f"Уточните {ask}.", evidence=self.ev("days_named", "hours_named"))
        elif ref is not None:
            self.issue("SCH-1", "schedule", "ambiguity", ref.field, ref.quote(),
                       "График работы не указан, сведения отложены.", f"Уточните {ask}.",
                       evidence=self.ev("days_named", "hours_named"))
        else:
            self.issue("SCH-1", "schedule", "missing", "schedule", "", "График работы не указан.",
                       f"Уточните {ask}.", evidence=self.ev("days_named", "hours_named"))

    def employment(self):
        f = self.f
        emp = norm(f.employment).lower()
        fs = f.schedule
        if not emp.strip() and not f.schedule_raw.strip() and not f.of("part") and not f.of("temp") \
                and not re.search(r"занятост|ставк|совместител|подработ|полный\s+день|неполн", f.free_text, re.I):
            self.issue("EMP-4", "employment", "missing", "employment", "",
                       "Не указаны ни тип занятости, ни график работы.",
                       "Уточните тип занятости (полная, частичная, временная) и объём работы.")
        volume, fmt = self.a("volume_text"), self.a("format")
        part = f.of("part")
        if "полн" in emp and "неполн" not in emp and fs.regime == "part":
            self.issue("EMP-1", "employment", "contradiction", "employment", f.employment,
                       "Занятость указана как полная, а график — неполный рабочий день.",
                       "Уточните размер ставки и количество рабочих часов в неделю.",
                       field_2="schedule", quote_2=f.schedule_raw)
        elif "полн" in emp and "неполн" not in emp and part and (
                volume in ("part", "part_sized") or (volume is None and any(F.STRONG_PART.search(c.sent.text) for c in part))):
            s = next((c.sent for c in part if F.STRONG_PART.search(c.sent.text)), part[0].sent)
            self.issue("EMP-1", "employment", "contradiction", "employment", f.employment,
                       "В поле занятости указана полная занятость, а в тексте — неполная работа или подработка.",
                       "Уточните, полная это занятость или частичная, и сколько часов нужно работать.",
                       field_2=s.field, quote_2=s.quote(), evidence=self.ev("volume_text"))
        elif "частичн" in emp and (volume == "full" or (fs.full and fs.regime in ("full", "day5") and not part)
                                   or fs.weekly_hours >= self.p["full_time_hours"]):
            cand = next((c for c in f.of("schedule") if c.info.has_params), None)
            fld, quote = (cand.sent.field, cand.sent.quote()) if cand and not fs.has_params else ("schedule", f.schedule_raw)
            self.issue("EMP-1", "employment", "contradiction", "employment", f.employment,
                       "Занятость указана как частичная, а график соответствует полному рабочему времени.",
                       "Уточните, полная это занятость или частичная.",
                       field_2=fld, quote_2=quote, evidence=self.ev("volume_text"))
        remote, onsite = f.of("remote"), f.of("onsite")
        if re.search(r"дистанц|удал[её]н", emp) and onsite and fmt in ("onsite", "conflict"):
            s = onsite[0].sent
            self.issue("EMP-1", "employment", "contradiction", "employment", f.employment,
                       "В поле занятости указана дистанционная работа, а в тексте — обязательное присутствие на месте.",
                       "Уточните, работа дистанционная или на месте.",
                       field_2=s.field, quote_2=s.quote(), evidence=self.ev("format"))
        elif remote and onsite and fmt == "conflict" and remote[0].sent.n != onsite[0].sent.n:
            a, b = remote[0].sent, onsite[0].sent
            self.issue("LOC-5", "location", "contradiction", a.field, a.quote(),
                       "В одном месте вакансии работа названа дистанционной, в другом требуется присутствие на месте.",
                       "Уточните формат работы: дистанционно, на месте или совмещение, и по каким правилам.",
                       field_2=b.field, quote_2=b.quote(), evidence=self.ev("format"))
        temp = f.of("temp")
        if temp and self.a("term_kind") == "temporary" and not re.search(r"временн|сезонн|стажир|срочн", emp):
            s = temp[0].sent
            if f.employment:
                self.issue("EMP-3", "employment", "contradiction", "employment", f.employment,
                           "В поле занятости работа не отмечена как временная, а в тексте она ограничена сроком.",
                           "Уточните, постоянная это работа или на определённый срок, и отразите это в поле занятости.",
                           field_2=s.field, quote_2=s.quote(), evidence=self.ev("term_kind"))
            else:
                self.issue("EMP-3", "employment", "ambiguity", s.field, s.quote(),
                           "Работа ограничена сроком, но тип занятости не указан.",
                           "Уточните тип занятости и срок работы.", evidence=self.ev("term_kind"))
        if re.search(r"временн|сезонн|стажир|срочн", emp) and not F.TEMP_CUE.search(f.free_text) \
                and not re.search(r"\d+\s*(мес|недел|дн)|до\s+\d", f.free_text, re.I):
            self.issue("EMP-3", "employment", "ambiguity", "employment", f.employment,
                       "Работа временная, но не сказано, на какой срок и по какой причине.",
                       "Уточните срок работы (период или дату окончания) и её характер: сезон, замещение, проект.")

    def location(self):
        f = self.f
        for address in f.addresses:
            found = F.settlements(norm(address))
            names = {}
            for name, a, b in found:
                names.setdefault(F.stem(name), (a, b))
            if len(names) >= 2:
                (a1, b1), (a2, b2) = list(names.values())[:2]
                self.issue("LOC-4", "location", "contradiction", "addresses.address", address[a1:b1],
                           "В адресе названы два разных населённых пункта.",
                           "Уточните, в каком населённом пункте находится место работы.",
                           field_2="addresses.address", quote_2=address[a2:b2])
                break
        other = [c for c in f.of("settlement") if not c.info["same"]]
        if f.address_settlements and other and self.a("place_conflict") == "work":
            c = other[0]
            self.issue("TTL-4" if c.sent.field == "job-name" else "LOC-2", "location", "contradiction",
                       "addresses.address", f.addresses[0],
                       "В поле адреса указан один населённый пункт, а в другом месте вакансии местом работы назван другой.",
                       "Уточните фактическое место работы и исправьте адрес или текст.",
                       field_2=c.sent.field, quote_2=c.sent.quote(), evidence=self.ev("place_conflict"))
        if self.a("multi_place") == "not_explained":
            self.issue("LOC-7", "location", "ambiguity", "addresses.address", f.addresses[1] if len(f.addresses) > 1
                       else f.addresses[0],
                       "Указано несколько адресов в разных населённых пунктах, но не сказано, где основное место работы.",
                       "Уточните основное место работы и как часто нужно бывать по другим адресам.",
                       evidence=self.ev("multi_place"))
        refs = self.refs_to("place")
        if not f.address_settlements:
            named = {F.stem(c.value) for c in f.of("settlement") if c.sent.field != "job-name"}
            if len(named) >= 2:
                s = next(c.sent for c in f.of("settlement") if c.sent.field != "job-name")
                self.issue("LOC-1", "location", "ambiguity", s.field, s.quote(),
                           "В адресе населённый пункт не указан, а в тексте названо несколько возможных мест работы.",
                           "Уточните, в каком населённом пункте и по какому адресу предстоит работать.",
                           evidence=self.ev("place_named"))
            elif not other and not f.of("settlement") and self.a("place_named") != "yes":
                if f.addresses:
                    self.issue("LOC-1", "location", "ambiguity", "addresses.address", f.addresses[0],
                               "Место работы указано без населённого пункта.",
                               "Уточните населённый пункт и адрес, где предстоит работать.", evidence=self.ev("place_named"))
                elif refs:
                    self.issue("LOC-1", "location", "ambiguity", refs[0].field, refs[0].quote(),
                               "Место работы не указано, сведения отложены.",
                               "Уточните населённый пункт и адрес, где предстоит работать.", evidence=self.ev("ref_1", "ref_2"))
                else:
                    self.issue("LOC-1", "location", "missing", "addresses.address", "",
                               "Место работы не указано ни в поле адреса, ни в тексте.",
                               "Уточните населённый пункт и адрес или территорию, где выполняется работа.",
                               evidence=self.ev("place_named"))
        elif refs:
            self.issue("LOC-6", "location", "ambiguity", refs[0].field, refs[0].quote(),
                       "Адрес или формат рабочего места не названы: сведения отложены до собеседования.",
                       "Уточните адрес рабочего места и формат работы (на месте, дистанционно, разъезды).",
                       evidence=self.ev("ref_1", "ref_2"))
        elif f.addresses and not any(_STREET.search(re.sub(r"г\.?\s*[А-ЯЁ]\S+", "", a)) for a in f.addresses):
            self.hint("LOC-3", "location", "addresses.address", f.addresses[0], "В адресе назван только населённый пункт.",
                      "Можно уточнить улицу и дом или название объекта.")

    def finish(self):
        issues, seen_gap, seen = [], set(), set()
        for it in self.issues:
            if any(re.search(pat, it["problem"] + " " + it["recommendation"], re.I) for pat in FORBIDDEN):
                continue                                  # страховка: шаблоны таких слов не содержат
            if it["type"] != "contradiction":
                if it["topic"] in seen_gap:
                    continue                              # одно замечание о неполноте на тему
                seen_gap.add(it["topic"])
            key = (it["rule"], it["field"], it["quote"], it.get("quote_2"))
            if key in seen:
                continue
            seen.add(key)
            issues.append(it)
        flagged = {it["topic"] for it in issues}
        hints, seen_hint = [], set()
        for h in self.hints:
            if h["topic"] in flagged or h["topic"] in seen_hint:
                continue                                  # подсказка не дублирует замечание по той же теме
            seen_hint.add(h["topic"])
            hints.append(h)
        return issues, hints


def decide(f: F.Facts, answers, policy: dict):
    """Замечания и подсказки по одной вакансии. answers = None, если ИИ недоступен."""
    return Decision(f, answers, policy).run()
