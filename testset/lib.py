"""Общие части тестового набора: каталог правил, конструкторы случаев, сборка вакансии.

Правила описаны в docs/criteria.md; здесь только их идентификаторы и краткие названия.
"""
import copy
import uuid
import zlib

TOPICS = ["title", "duties", "requirements", "salary", "schedule", "location", "employment"]
TYPES = ["missing", "ambiguity", "contradiction"]
NS = uuid.UUID("7e570000-0000-4000-8000-000000000000")
DROP = object()

# Значения справочников портала (как в API).
FULL = "Полный рабочий день"
SHIFT = "Сменная работа"
PART = "Неполный рабочий день/неполная рабочая неделя"
FLEX = "Режим гибкого рабочего времени"
IRREG = "Ненормированный рабочий день"
ROTA = "Вахтовый метод"
NO_EDU = "Tребования не предъявляются"  # в API первая буква латинская, это не опечатка
EMP_FULL = "Полная занятость"
EMP_PART = "Частичная занятость"
EMP_TEMP = "Временная работа"
EMP_REMOTE = "Дистанционная (удаленная) работа"
EMP_INTERN = "Стажировка"

# kind: must — замечание обязательно; flag / skip — спорное правило и наш выбор по умолчанию.
RULES = {
    "TTL-1": ("title", "must", "Безликое название, обязанности его не раскрывают"),
    "TTL-2": ("title", "must", "Название и обязанности о разных профессиях"),
    "TTL-3": ("title", "must", "Несколько разных профессий в названии, обязанности покрывают не все"),
    "TTL-4": ("title", "must", "Сведения в названии спорят с полем другой темы"),
    "DUT-1": ("duties", "must", "Только отсылка или заглушка"),
    "DUT-2": ("duties", "must", "Не задачи: личные качества, реклама, льготы"),
    "DUT-3": ("duties", "must", "Пусто и в duty, и в skills"),
    "DUT-4": ("duties", "must", "Только «поручения руководителя»"),
    "REQ-1": ("requirements", "skip", "Образование не определено ни в поле, ни в тексте"),
    "REQ-2": ("requirements", "must", "Опыт в поле расходится с определённым числом в тексте"),
    "REQ-3": ("requirements", "must", "Минимальный уровень образования в поле и тексте разный"),
    "REQ-4": ("requirements", "must", "Поле qualification спорит с полем опыта"),
    "REQ-5": ("requirements", "skip", "Размытые формулировки, отраслевые детали"),
    "REQ-6": ("requirements", "flag", "Регламентированная профессия без требований"),
    "REQ-7": ("requirements", "must", "Одно требование в двух местах описано противоположно"),
    "PAY-1": ("salary", "must", "Суммы нет нигде или только отсылка"),
    "PAY-2": ("salary", "must", "Итоговая сумма в тексте вне допуска от вилки"),
    "PAY-3": ("salary", "must", "Минимум больше максимума"),
    "PAY-4": ("salary", "must", "Вилка шире порога без объяснения"),
    "PAY-5": ("salary", "must", "Единица оплаты неясна"),
    "PAY-6": ("salary", "flag", "Сумма при неполном времени без доли ставки"),
    "PAY-7": ("salary", "skip", "Состав оплаты без влияния на сумму, коды премий"),
    "SCH-1": ("schedule", "must", "Открытый режим без параметров"),
    "SCH-2": ("schedule", "must", "Режим в поле расходится с текстом"),
    "SCH-3": ("schedule", "must", "Дневная смена в поле, ночная в тексте (и наоборот)"),
    "SCH-4": ("schedule", "must", "Два взаимоисключающих графика"),
    "SCH-5": ("schedule", "flag", "Параметры частичные или даны вариантами"),
    "SCH-6": ("schedule", "skip", "Закрытый режим без часов"),
    "SCH-7": ("schedule", "must", "Работа вне графика без частоты и условий"),
    "SCH-8": ("schedule", "must", "Часы в день не сходятся с заявленной недельной нормой"),
    "LOC-1": ("location", "must", "Нет населённого пункта"),
    "LOC-2": ("location", "must", "Адрес и названное в тексте место работы в разных населённых пунктах"),
    "LOC-3": ("location", "skip", "Разъезды, удалённая работа, несколько адресов"),
    "LOC-4": ("location", "must", "Внутри строки адреса два разных населённых пункта"),
    "LOC-5": ("location", "must", "Дистанционная работа и обязательное присутствие в одной вакансии"),
    "LOC-6": ("location", "must", "Адрес или формат рабочего места отложены до собеседования"),
    "LOC-7": ("location", "must", "Несколько адресов в разных населённых пунктах без указания основного"),
    "EMP-1": ("employment", "must", "Поле занятости расходится с объёмом работы"),
    "EMP-2": ("employment", "skip", "Поля занятости нет"),
    "EMP-3": ("employment", "must", "Работа на срок, а поле занятости этого не отражает"),
}

HOURS = [
    "Режим работы: понедельник–пятница с 9:00 до 18:00, перерыв с 13:00 до 14:00.",
    "График: пятидневная рабочая неделя, с 8:30 до 17:30, суббота и воскресенье — выходные.",
    "Рабочее время: пн–пт с 8:00 до 17:00, обед с 12:00 до 13:00.",
    "Работа с понедельника по пятницу, с 9:00 до 18:00.",
]
EMPLOYERS = [
    ('ГБУЗ КО "Учебная городская больница"', "Здравоохранение и социальное обеспечение"),
    ('МАОУ "Учебная средняя школа"', "Образование, наука"),
    ('МКУ "Учебный центр обслуживания населения"', "Государственная служба, некоммерческие организации"),
    ('МУП "Учебный водоканал"', "ЖКХ"),
    ('МАДОУ "Учебный детский сад"', "Образование, наука"),
    ("Администрация учебного городского округа", "Государственная служба, некоммерческие организации"),
]
# Поле qualification заполнено у 97% реальных вакансий, обычно ничего не значащим текстом.
QUALIFICATION_FILLERS = [None, ".", "соответствие занимаемой должности", None]
DEFAULT_REQ = "Среднее профессиональное образование по профилю работы, опыт работы от 1 года."
DEFAULT_ADDR = "Калининградская область, г Калининград, Учебная улица, дом: 1;"

# Латинские буквы, которыми в реальных вакансиях подменяют кириллические.
_LOOKALIKE = str.maketrans("aceopxyAKMHOPCTXBE", "асеорхуАКМНОРСТХВЕ")
_TO_LATIN = str.maketrans("асеорх", "aceopx")

CASES = []


def norm(text):
    """Заменяет латинские буквы-двойники кириллическими; длина строки не меняется."""
    return text.translate(_LOOKALIKE)


def latinize(text):
    """Подменяет часть кириллических букв латинскими двойниками, как в реальных вакансиях."""
    return "".join(ch.translate(_TO_LATIN) if i % 3 == 0 else ch for i, ch in enumerate(text))


def must(rule, topic, type_, fields, problem, rec, all_=(), any_=()):
    """Обязательное замечание. topic и type_ допускают варианты через «|».

    all_ — группы цитат, из каждой группы в замечании должна быть хотя бы одна
    (для противоречия это две стороны). any_ — достаточно любой одной цитаты.
    """
    return {
        "rule": rule,
        "topic": topic.split("|"),
        "type": type_.split("|"),
        "fields": list(fields),
        "quotes_all": [list(g) for g in all_],
        "quotes_any": list(any_),
        "problem": problem,
        "recommendation": rec,
    }


def opt(rule, lean, note):
    """Спорная тема: правило, наш выбор по умолчанию (flag — выводим, skip — нет) и пояснение."""
    assert lean in ("flag", "skip"), lean
    return {"rule": rule, "lean": lean, "note": note}


def case(code, intent, job, duty, hours=True, req: str | None = DEFAULT_REQ, set_=None, drop=(),
         must_=(), optional=None, neg=()):
    """Регистрирует синтетический случай.

    neg — правила, для которых этот случай служит отрицательным примером:
    выглядит похоже на нарушение правила, но замечания быть не должно.
    """
    CASES.append(dict(code=code, intent=intent, job=job, duty=duty, hours=hours, req=req,
                      set=set_ or {}, drop=list(drop), must=list(must_), optional=optional or {},
                      neg=list(neg)))


def put(obj, path, value):
    keys = path.split(".")
    for k in keys[:-1]:
        obj = obj.setdefault(k, {})
    if value is DROP:
        obj.pop(keys[-1], None)
    else:
        obj[keys[-1]] = value


def build(c):
    """Собирает вакансию в формате API из описания случая."""
    h = zlib.crc32(c["code"].encode())
    vid = str(uuid.uuid5(NS, c["code"]))
    name, spec = EMPLOYERS[h % len(EMPLOYERS)]
    duty = c["duty"] + (" " + HOURS[h % len(HOURS)] if c["hours"] else "")
    v = {
        "id": vid,
        "source": "Вакансия работодателя",
        "region": {"region_code": "3900000000000", "name": "Калининградская область"},
        "company": {"companycode": "0000000000000", "email": "kadry@example.org", "hr-agency": False,
                    "inn": "0000000000", "kpp": "000000000", "name": name, "ogrn": "0000000000000",
                    "url": "https://trudvsem.ru/company/0000000000000"},
        "creation-date": "2026-11-02",
        "date_modify": "2026-11-20T10:00:00+0300",
        "salary": "от 45000", "salary_min": 45000, "salary_max": 45000,
        "job-name": c["job"],
        "vac_url": f"https://trudvsem.ru/vacancy/card/0000000000000/{vid}",
        "employment": EMP_FULL,
        "schedule": FULL,
        "requirements": c["req"],
        "duty": duty,
        "category": {"specialisation": spec},
        "requirement": {"education": "Среднее профессиональное образование", "experience": 1},
        "addresses": {"address": [{"location": DEFAULT_ADDR, "lng": "20.5", "lat": "54.7"}]},
        "social_protected": "",
        "contact_list": [{"contact_type": "Телефон", "contact_value": "+7(000) 000-00-00"}],
        "contact_person": "Специалист по кадрам",
        "work_places": 1,
        "skills": [],
        "workPlaceType": {"workPlaceForeign": False, "workPlaceOrdinary": True,
                          "workPlaceQuota": False, "workPlaceSpecial": False},
        "shift": [],
        "trainingDays": 0,
        "currency": "«руб.»",
    }
    filler = QUALIFICATION_FILLERS[(h >> 3) % len(QUALIFICATION_FILLERS)]
    if filler:
        v["qualification"] = filler
    if (h >> 5) % 2:
        v["typicalPosition"] = "Ничего не выбрано"
    if c["req"] is None:
        v.pop("requirements")
    for path, value in c["set"].items():
        put(v, path, copy.deepcopy(value))
    for path in c["drop"]:
        put(v, path, DROP)
    return v


def sal(lo, hi=None):
    """Поля зарплаты. Строка salary на портале всегда имеет вид «от N», даже при нуле."""
    hi = lo if hi is None else hi
    return {"salary": f"от {lo}", "salary_min": lo, "salary_max": hi}


def addr(*locations):
    return {"addresses.address": [{"location": x, "lng": "20.5", "lat": "54.7"} for x in locations]}
