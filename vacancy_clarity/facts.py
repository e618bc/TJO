"""Факты о вакансии, которые код устанавливает сам, без ИИ.

Здесь нет решений о замечаниях: модуль только собирает источники по темам, оценивает
структурированные поля и находит в тексте кандидатов (суммы, сроки опыта, графики,
населённые пункты, отсылки). Что они значат, спрашивается у ИИ в questions.py,
а решение принимается в decide.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .textnorm import Sentence, norm, sentences

# Поля, которые не несут сведений по семи темам и в текст для анализа не попадают.
_SKIP_PREFIXES = ("company", "contact", "region", "category", "workPlaceType", "addresses")
_SKIP_KEYS = {"id", "source", "vac_url", "creation-date", "date_modify", "currency", "salary", "salary_min",
              "salary_max", "social_protected", "code_profession", "typicalPosition", "conditions", "hireDate",
              "medicalDocuments", "medicalCertificate", "benefit", "employment", "work_places", "trainingDays",
              "requirement.education", "requirement.experience", "addressOffice", "shift"}
# Порядок, в котором известные текстовые поля показываются модели.
_TEXT_ORDER = ["duty", "requirements", "requirement.qualification", "qualification", "schedule",
               "scheduleTypeComment", "term.text", "benefitDetails", "skills"]
_FILLER = re.compile(r"^[\s.\-–—_/\\,;:!?*0-9]*$")
_PLACEHOLDER = re.compile(r"^\W*(не\s+указан[оаы]?|нет|отсутству\w*|без\s+уточнени[яй]|не\s+заполнен[оа]?|н/д|"
                          r"уточняется|по\s+договор[её]нности)\W*$", re.I)
_PLACEHOLDER_COMMENT = re.compile(r"^(без уточнени[яй]|не указан[оа]?|нет|-+|\.+)$", re.I)

PAY_WORD = re.compile(r"зарплат|заработн|з/п|\bзп\b|оклад|оплат|доход|ставк|тариф|преми|надбав|выплат|руб|₽|т\.\s?р",
                      re.I)
_NUMBER = re.compile(r"(?<![\d/.,:№-])(\d{1,3}(?:[   ]\d{3})+|\d{2,7})(?![\d/:]|[.,]\d)")
_THOUSANDS = re.compile(r"^\s*(т\.\s?р|тыс|тысяч|к\b)", re.I)

EXP_WORD = re.compile(r"опыт|стаж", re.I)
_EXP_RANGE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)\s*(?:лет|года|год)\b", re.I)
_EXP_NUM = re.compile(r"(\d+)\s*(?:\(\w+\))?\s*(?:[-–]?\s?(?:х|ти|и|го|ми|ёх|ех))?\s*(лет|года|год|мес)", re.I)
_EXP_WORDS = [(re.compile(r"пол\s?года|полугода|полгода", re.I), 0.5),
              (re.compile(r"(одного|1-го|не менее|от)\s+года\b", re.I), 1.0),
              (re.compile(r"двух\s+лет", re.I), 2.0), (re.compile(r"тр[её]х\s+лет", re.I), 3.0),
              (re.compile(r"пяти\s+лет", re.I), 5.0)]
EXP_NOT_CONSIDERED = re.compile(r"без опыта\s+(работы\s+)?не\s+рассматрива", re.I)
EXP_NOT_REQUIRED = re.compile(
    r"без опыта|опыт\w*\s+(работы\s+)?не\s+(требуется|обязател\w+|нужен)|опыт\s+необязател", re.I)
EXP_SOFT = re.compile(r"желател|приветств|преимуществ|плюсом|будет плюс", re.I)

SCHEDULE_WORD = re.compile(
    r"график|режим\s+работы|рабоч\w+\s+(время|день|дни|недел)|смен|ночн|в\s+ночь|вахт|сутки|суточн|выходн|"
    r"\b\d{1,2}\s*/\s*\d{1,2}\b|\d{1,2}[:.]\d{2}|пятидневн|шестидневн|понедельник|пн\s*[-–—]\s*пт|"
    r"час\w*\s+в\s+(день|неделю)|\d+\s*[- ]?час|неполн\w+\s+(рабоч|день|недел)|гибк|ненормирован|дежурств", re.I)
_RATIO = re.compile(r"(?<![\d.,/])(\d{1,2})\s*/\s*(\d{1,2})(?![\d/])")
_KNOWN_RATIOS = {(5, 2), (6, 1), (2, 2), (1, 2), (1, 3), (3, 3), (2, 1), (4, 2), (4, 3), (3, 2), (7, 7), (5, 5),
                 (10, 10), (14, 14), (15, 15), (20, 10), (30, 30), (30, 15), (45, 45), (60, 30), (1, 1), (3, 1), (4, 4)}
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s?[:.]\s?([0-5]\d)(?!\d)")
_TIME_RANGE_WORDS = re.compile(r"\bс\s+(\d{1,2})\s+до\s+(\d{1,2})\b", re.I)
_DAY5 = re.compile(r"пятидневн|5\s?[- ]?ти\s?дневн|пн\s*[-–—]\s*пт|понедельник\w*\s*[-–—]\s*пятниц|"
                   r"с\s+понедельника\s+по\s+пятницу|будн", re.I)
_WEEKDAY = re.compile(r"понедельник|вторник|сред[ау]|четверг|пятниц|суббот|воскресень|\bпн\b|\bвт\b|\bср\b|\bчт\b|"
                      r"\bпт\b|\bсб\b|\bвс\b", re.I)
_SHIFT_PATTERN = re.compile(r"смен\w*\s+график|график\w*\s+смен|посменн|сменн\w+\s+работ|чередовани\w+.{0,90}смен|"
                            r"(перв|втор|треть|утренн|вечерн|дневн\w+\s+и\s+ночн)\w*\s+(и\s+\w+\s+)?смен|в\s+(\d|две|три)\s+смен|"
                            r"\d\s*[- ]?сменн", re.I)
_NIGHT = re.compile(r"ночн\w+\s+(смен|дежурств|работ|врем|график)|работ\w*\s+(в\s+)?ночн|в\s+ночь|ночью|"
                    r"(день|дневн\w+)\s*/\s*ночь|\bс\s+(19|2[0-3])[:.]\d\d\s+до\s+0?[0-9][:.]\d\d", re.I)
_DAY_ONLY = re.compile(r"дневн\w+\s+смен|только\s+дн[её]м|в\s+дневное\s+время", re.I)
_SUTKI = re.compile(r"сутки\s+через|суточн", re.I)
_ROTA = re.compile(r"вахтов\w+\s+(метод|график|режим|работ)|вахта\s+\d|вахтами|на\s+вахту|вахтой|вахтовик", re.I)
_HOURS_LEN = re.compile(r"(\d{1,2})\s*[- ]?\s*час", re.I)
_FLEX = re.compile(r"гибк|свободн\w+\s+график", re.I)
_PART = re.compile(r"неполн\w+\s+(рабоч\w+\s+)?(день|дня|недел)|неполн\w+\s+занятост|сокращ[её]нн\w+\s+(рабоч\w+\s+)?д",
                   re.I)
_IRREG = re.compile(r"ненормирован", re.I)
_FULL = re.compile(r"полн\w+\s+рабоч\w+\s+д|полный\s+день", re.I)

PART_CUE = re.compile(r"подработ|совместител|0[,.]\d+\s*ставк|полставк|пол\s+ставк|\d\s*[-–]?\s*\d?\s*час\w*\s+в\s+день|"
                      r"частичн\w+\s+занятост|неполн\w+\s+занятост", re.I)
REMOTE_CUE = re.compile(r"дистанцион|удал[её]нн|из\s+дома|на\s+дому|из\s+любого\s+(города|места)", re.I)
ONSITE_CUE = re.compile(r"в\s+офисе|присутстви\w+.{0,50}(офис|рабоч\w+\s+мест|обязательн)|"
                        r"на\s+рабочем\s+месте\s+обязательн|только\s+очно|очн\w+\s+формат", re.I)
REFERENCE = re.compile(r"собеседовани|по\s+договор[её]нности|договорн|обсужда\w+|уточня\w+|по\s+согласовани|"
                       r"по\s+результатам|индивидуальн\w+\s+порядке", re.I)
TRAINING = re.compile(r"обучени\w+\s+(предоставля|провод|за\s+сч[её]т|на\s+(рабочем\s+)?месте)|научим|обучим|"
                      r"обучаем|остальному\s+науч", re.I)
NET_PAY = re.compile(r"на\s+руки|после\s+(вычета|удержани)|за\s+вычетом|чистыми|без\s+учёта\s+налог|без\s+учета\s+налог", re.I)
EDU_LEVELS = (("high", re.compile(r"высш", re.I)),
              ("spo", re.compile(r"средн\w*[\s-]+(проф|спец|медицин|педагог|техническ|фармацевт)|\bспо\b|"
                                 r"начальн\w+\s+проф", re.I)),
              ("school", re.compile(r"средн\w*\s+общ|общ\w+\s+средн|основн\w+\s+общ|школ", re.I)))
EDU_RANK = {"school": 1, "spo": 2, "high": 3}
# «среднее» без уточнения: может означать и общее, и профессиональное — по нему уровень не определяется
EDU_BARE_MIDDLE = re.compile(r"средн\w*(?![\s-]+(проф|спец|медицин|педагог|техническ|фармацевт|общ))(?=\W)", re.I)
STRONG_PART = re.compile(r"подработ|совместител|0[,.]\d+\s*ставк|полставк|пол\s+ставк|"
                         r"(?<!\d)[1-6](\s*[-–]\s*[1-6])?\s*час\w*\s+в\s+день", re.I)
PAY_UNIT = re.compile(r"почасов|за\s+(один\s+)?час|в\s+час\b|за\s+смен|за\s+выход|сдельн|выработк|"
                      r"процент\w*\s+от\s+(продаж|выручк)|%\s+от\s+(продаж|выручк)|за\s+единиц|по\s+расценк", re.I)
TEMP_CUE = re.compile(r"сезонн|на\s+(отопительный\s+|летний\s+|зимний\s+)?сезон|на\s+период|срочн\w+\s+(трудов\w+\s+)?договор|"
                      r"временн\w+\s+(работ|договор|трудоустр)|на\s+время\s+(отпуск|декрет)|декретн\w+\s+ставк|"
                      r"с\s+\d{1,2}\s+\w+\s+по\s+\d{1,2}\s+\w+|на\s+\d+\s+(месяц|мес)", re.I)
EXTRA_TIME = re.compile(r"в\s+нерабочее\s+время|вне\s+(основного\s+)?(рабочего\s+)?(времени|графика)|сверхурочн|"
                        r"по\s+вызову|выход\w*\s+в\s+выходн|работ\w*\s+в\s+выходн\w+\s+(дни\s+)?по\s+необходимост|"
                        r"привлеч\w+\s+к\s+работе\s+в\s+выходн|дежурств\w*\s+по\s+необходимост|"
                        r"с\s+работой\s+в\s+выходн|работ\w*\s+в\s+(выходные\s+и\s+)?праздничн", re.I)
_REQ_NEG = re.compile(r"не\s+требу|не\s+обязател|не\s+нуж|необязател|без\s+(наличия|опыта|образовани|удостоверени)", re.I)
_REQ_POS = re.compile(r"обязател|требу|необходим|наличие|нужн|не\s+менее|\bот\s+\d", re.I)
_REQ_TOPICS = [re.compile(x, re.I) for x in (
    r"водительск\w+\s+удостоверени|категори\w+\s+[ABCDEВСДЕ]\b|права\s+категор", r"медицинск\w+\s+книжк|медкнижк",
    r"справк\w+\s+об?\s+(отсутствии|наличии)\s+судимост|судимост", r"высше\w*\s+образовани", r"опыт\w*\s+работ",
    r"удостоверени\w+\s+(оператора|сварщика|тракториста|охранник)|групп\w+\s+по\s+электробезопасност",
    r"знани\w+\s+английск|иностранн\w+\s+язык", r"аккредитаци|сертификат\w*\s+специалист")]
WEEKLY_NORM = re.compile(r"(\d{2})\s*[-–]?\s*час\w*\s+(рабоч\w+\s+)?недел|продолжительност\w+\s+рабоч\w+\s+недел\w*\s+"
                         r"(\d{2})\s*час|(\d{2})\s*час\w*\s+в\s+неделю", re.I)
_LUNCH = re.compile(r"(обед|перерыв)\w*\s*(\d{1,3})\s*мин|(обед|перерыв)\w*\s*(1|один|час)\s*ч|"
                    r"(обед|перерыв)\w*\s*(с\s*)?(\d{1,2})[:.](\d{2})\s*[-–—до ]+\s*(\d{1,2})[:.](\d{2})", re.I)
DUTY_SECTION = re.compile(r"чем\s+нужно\s+заниматься|обязанности\s*:|функциональн\w+\s+обязанност|что\s+нужно\s+делать",
                          re.I)


def weekly_check(text: str):
    """(заявленная недельная норма, расчёт по часам) или None. Учебная нагрузка и ставки не считаются."""
    low = norm(text).lower()
    m = WEEKLY_NORM.search(low)
    if not m or re.search(r"нагрузк|ставк|учебн|педагогич|аудиторн", low):
        return None
    stated = int(next(g for g in m.groups() if g and g.isdigit()))
    info = schedule_info(low)
    if not info.daily_hours or not (info.day5 or (5, 2) in info.ratios):
        return None
    lunch = 0.0
    lm = _LUNCH.search(low)
    if lm:
        if lm.group(2):
            lunch = int(lm.group(2)) / 60
        elif lm.group(4):
            lunch = 1.0
        elif lm.group(7):
            lunch = ((int(lm.group(9)) * 60 + int(lm.group(10))) - (int(lm.group(7)) * 60 + int(lm.group(8)))) / 60
    elif re.search(r"обед|перерыв", low):
        lunch = 1.0
    return stated, round((info.daily_hours - lunch) * 5, 1)


DUTY_REFERENCE = re.compile(r"должностн\w+\s+инструкци|трудов\w+\s+договор\w*|по\s+договор[её]нности", re.I)

_SETTLE_PREFIX = re.compile(
    r"(?<![а-яёА-ЯЁ])(г\.|г(?=\s)|гор\.|город[еа]?|пос\.|пос[её]л(?:ок|ке|ка)|п\.|пгт|с\.|село|селе|дер\.|деревн[яе])"
    r"\s*([А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)")
_SETTLE_POSTFIX = re.compile(
    r"([А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*)(?:\s[А-ЯЁ][а-яё]+)?\s+(пос[её]лок|село|деревня|станция|хутор)(?![а-яё])")
_NOT_SETTLEMENT = {"калининградская", "калининградской", "россия", "российская", "обл"}


def as_int(value: Any) -> int:
    """Число из поля, даже если оно пришло строкой или отсутствует."""
    try:
        return int(float(str(value).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def get(obj: Any, path: str, default: Any = None) -> Any:
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return default if obj is None else obj


def is_filler(value: Any) -> bool:
    """Поле пустое по смыслу: пробелы, знаки, цифры или заглушка вида «Не указано»."""
    return not isinstance(value, str) or bool(_FILLER.match(value)) or bool(_PLACEHOLDER.match(norm(value)))


def stem(name: str) -> str:
    """Грубая основа названия населённого пункта: «Советске» и «Советск» совпадают."""
    word = norm(name).lower().replace("ё", "е")
    word = re.sub(r"(ого|ому|ом|ой|ая|ую|е|а|у|ы|и|я)$", "", word) if len(word) > 5 else word
    return word[:7]


def settlements(text: str) -> list[tuple[str, int, int]]:
    """Населённые пункты, названные в строке: (название, начало, конец)."""
    found = []
    for m in _SETTLE_PREFIX.finditer(text):
        name = m.group(2)
        is_city = m.group(1).lower().startswith("г")          # «г. Пионерский» — город, хоть и на «-ский»
        if name.lower() not in _NOT_SETTLEMENT and (is_city or not name.lower().endswith(("ский", "ская", "ское", "ского"))):
            found.append((name, m.start(), m.end()))
    for m in _SETTLE_POSTFIX.finditer(text):
        name = m.group(1)
        if name.lower() not in _NOT_SETTLEMENT and not name.lower().endswith(("ский", "ская", "ское")):
            found.append((name, m.start(), m.end()))
    return found


@dataclass
class ScheduleInfo:
    """Что код видит в одном утверждении о графике."""
    ratios: list = field(default_factory=list)
    times: list = field(default_factory=list)
    day5: bool = False
    weekdays: bool = False
    hours_len: bool = False
    sutki: bool = False
    shift: bool = False
    night: bool = False
    day_only: bool = False
    rota: bool = False
    flex: bool = False
    part: bool = False
    irregular: bool = False
    full: bool = False
    weekend_work: bool = False

    @property
    def has_days(self) -> bool:
        return bool(self.ratios or self.day5 or self.weekdays or self.sutki)

    @property
    def has_hours(self) -> bool:
        return bool(self.times or self.hours_len or self.sutki)

    @property
    def has_params(self) -> bool:
        return self.has_days or self.has_hours

    @property
    def daily_hours(self) -> float:
        """Длительность рабочего дня по первой паре времён «с … до …»; 0, если не определить."""
        if len(self.times) < 2:
            return 0.0
        (h1, m1), (h2, m2) = ((int(t[:2]), int(t[3:])) for t in self.times[:2])
        span = (h2 * 60 + m2 - h1 * 60 - m1) % (24 * 60)
        return round(span / 60, 2)

    @property
    def weekly_hours(self) -> float:
        """Оценка часов в неделю: пятидневка × часы в день или 2/2 × длительность смены."""
        day = self.daily_hours
        if not day:
            return 0.0
        if self.day5 or (5, 2) in self.ratios:
            return day * 5
        if (2, 2) in self.ratios:
            return day * 3.5
        if (1, 3) in self.ratios or self.sutki:
            return 24 * 7 / 4 if day == 24 or self.sutki else day * 7 / 4
        if (6, 1) in self.ratios:
            return day * 6
        return 0.0

    def signature(self) -> set:
        """Набор конкретных признаков графика для сравнения двух утверждений."""
        sig = {f"t{t}" for t in self.times} | {f"r{a}/{b}" for a, b in self.ratios}
        if self.night:
            sig.add("night")
        if self.sutki:
            sig.add("sutki")
        return sig

    @property
    def regime(self) -> str:
        """Класс режима: day5, shift, rota, flex, part, irregular, full или пусто."""
        other_ratio = any(r not in ((5, 2), (6, 1)) for r in self.ratios)
        if self.rota:
            return "rota"
        if self.sutki or other_ratio or (self.shift and not self.day5):
            return "shift"
        if self.flex:
            return "flex"
        if self.part:
            return "part"
        if self.day5 or (5, 2) in self.ratios:
            return "day5"
        if self.irregular:
            return "irregular"
        if self.full:
            return "full"
        return ""


def schedule_info(text: str) -> ScheduleInfo:
    low = text.lower()
    info = ScheduleInfo()
    info.ratios = [r for r in ((int(a), int(b)) for a, b in _RATIO.findall(low)) if r in _KNOWN_RATIOS]
    info.times = [f"{int(h):02d}:{m}" for h, m in _TIME.findall(low)]
    words_range = _TIME_RANGE_WORDS.search(low)
    if not info.times and words_range:
        info.times = ["%02d:00" % int(x) for x in words_range.groups()]
    if len(info.times) < 2:           # одно время («к 09:00») — ещё не расписание
        info.times = info.times if re.search(r"\bс\s+\d|\bдо\s+\d", low) and info.times else []
    info.day5 = bool(_DAY5.search(low))
    info.weekdays = bool(_WEEKDAY.search(low))
    info.weekend_work = bool(re.search(r"(по|в)\s+суббот|(по|в)\s+воскресень|суббот\w+\s+(груп|дежур|смен)|"
                                       r"включая\s+выходн|в\s+выходные\s+дни\s+(работ|по\s+граф)", low))
    info.hours_len = bool(_HOURS_LEN.search(low)) and not re.search(r"\d\s*час\w*\s+(назад|езды)", low)
    info.sutki = bool(_SUTKI.search(low))
    info.shift = bool(_SHIFT_PATTERN.search(low)) or bool(re.search(r"посменн|сменн\w+\s+(график|режим|работ)|"
                                                                     r"по\s+сменам|график\w*\s+сменност", low))
    info.night = bool(_NIGHT.search(low))
    info.day_only = bool(_DAY_ONLY.search(low))
    info.rota = bool(_ROTA.search(low))
    info.flex = bool(_FLEX.search(low))
    info.part = bool(_PART.search(low))
    info.irregular = bool(_IRREG.search(low))
    info.full = bool(_FULL.search(low))
    return info


def education_level(value: Any) -> str:
    """Уровень из поля образования: empty, notreq, school, spo, high."""
    if not isinstance(value, str) or is_filler(value):
        return "empty"
    low = norm(value).lower()
    if re.search(r"не\s+указан|не\s+выбран|^нет$", low):
        return "empty"
    if re.search(r"не\s+предъявля|не\s+требу|без\s+образован", low):
        return "notreq"
    if "высш" in low:
        return "high"
    if re.search(r"средн\w*\s+(проф|спец)|\bспо\b|начальн\w+\s+проф", low):
        return "spo"
    if re.search(r"общее|средн|основн", low):
        return "school"
    return "empty"


@dataclass
class Candidate:
    kind: str              # pay, exp, schedule, part, remote, onsite, settlement, reference
    sent: Sentence
    value: Any = None      # сумма, срок в годах, название населённого пункта и т. п.
    info: Any = None


@dataclass
class Facts:
    vacancy: dict
    vid: str
    url: str
    title: str
    sentences: list            # все предложения свободного текста, пронумерованы с 1
    salary_min: int
    salary_max: int
    schedule_raw: str
    schedule: ScheduleInfo     # что сказано в самом поле графика (вместе со сменами и комментарием)
    shift_values: list
    employment: str
    education_raw: str
    education: str
    experience: int
    experience_present: bool
    qualification_flag: str    # with / without / пусто — справочные «С опытом», «Без опыта»
    addresses: list            # строки адресов
    address_settlements: list  # названия населённых пунктов из адресов
    candidates: list

    def of(self, kind: str) -> list:
        return [c for c in self.candidates if c.kind == kind]

    def field_sentences(self, name: str) -> list:
        return [s for s in self.sentences if s.field == name]

    @property
    def free_text(self) -> str:
        return " ".join(s.text for s in self.sentences)


def _text_fields(v: dict) -> list[tuple[str, str]]:
    """Текстовые поля вакансии: сначала известные, затем любые другие длинные строки."""
    out, seen = [], set()

    def add(path: str, value: Any) -> None:
        if isinstance(value, list):
            value = "; ".join(str(x) for x in value if isinstance(x, (str, int, float)))
        if not isinstance(value, str) or is_filler(value) or path in seen:
            return
        seen.add(path)
        out.append((path, value))

    for path in _TEXT_ORDER:
        value = get(v, path)
        if path == "qualification" and isinstance(value, str) and len(value) < 30:
            continue                      # короткое значение — справочник или заглушка
        if path == "scheduleTypeComment" and isinstance(value, str) and _PLACEHOLDER_COMMENT.match(norm(value).strip()):
            continue
        add(path, value)

    def walk(obj: Any, prefix: str) -> None:
        if isinstance(obj, dict):
            for k, val in obj.items():
                walk(val, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, str) and len(obj) >= 25:
            if prefix in _SKIP_KEYS or prefix in _TEXT_ORDER or prefix == "job-name":
                return
            if prefix.startswith(_SKIP_PREFIXES) or re.match(r"https?://", obj):
                return
            add(prefix, obj)

    walk(v, "")
    return out


def _pay_candidates(sent: Sentence) -> list[Candidate]:
    if not PAY_WORD.search(sent.text):
        return []
    out = []
    for m in _NUMBER.finditer(sent.text):
        value = int(re.sub(r"\D", "", m.group(1)))
        tail = sent.text[m.end():m.end() + 12]
        if _THOUSANDS.match(tail) and value < 1000:
            value *= 1000
        if re.match(r"\s*(%|процент|час|ч\.|лет|год|мес|дн|человек|чел|шт|кв|м\b|км)", tail, re.I) or value < 500:
            continue
        if 1990 <= value <= 2100 and re.match(r"\s*(г\.|год|учебн|[-–]\s*20)", tail, re.I):
            continue
        before = sent.text[max(0, m.start() - 14):m.start()].lower()
        bound = "from" if re.search(r"\bот\s*$", before) else "to" if re.search(r"\bдо\s*$", before) else ""
        out.append(Candidate("pay", sent, value, {"bound": bound}))
    return out


def experience_duration(text: str):
    """Срок опыта из фразы: (минимум, максимум) в годах или None."""
    m = _EXP_RANGE.search(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _EXP_NUM.search(text)
    if m:
        n = float(m.group(1))
        years = n / 12 if m.group(2).lower().startswith("мес") else n
        return years, years
    for rx, years in _EXP_WORDS:
        if rx.search(text):
            return years, years
    return None


def build(vacancy: dict) -> Facts:
    """Собирает факты об одной вакансии. Никогда не падает на отсутствующих и странных полях."""
    v = vacancy if isinstance(vacancy, dict) else {}
    title = get(v, "job-name", "")
    title = title if isinstance(title, str) else str(title)

    sents: list[Sentence] = []
    for path, value in _text_fields(v):
        sents.extend(sentences(path, value, first_n=len(sents) + 1))
    title_sent = Sentence(0, "job-name", 0, len(title), title, norm(title))

    schedule_raw = get(v, "schedule", "")
    schedule_raw = schedule_raw if isinstance(schedule_raw, str) else ""
    shift_values = [x for x in (get(v, "shift") or []) if isinstance(x, str)] if isinstance(get(v, "shift"), list) else []
    sched_field_text = norm(schedule_raw)
    comment = get(v, "scheduleTypeComment", "")
    if isinstance(comment, str) and not _PLACEHOLDER_COMMENT.match(norm(comment).strip()):
        sched_field_text += ". " + norm(comment)
    sched = schedule_info(sched_field_text)
    if any("дневн" in norm(x).lower() for x in shift_values):
        sched.day_only = True
    if any("ночн" in norm(x).lower() for x in shift_values):
        sched.night = True

    addresses = []
    raw_addr = get(v, "addresses.address", [])
    if isinstance(raw_addr, dict):
        raw_addr = [raw_addr]
    for item in raw_addr if isinstance(raw_addr, list) else []:
        loc = item.get("location") if isinstance(item, dict) else item
        if isinstance(loc, str) and not is_filler(loc):
            addresses.append(loc)
    addr_settlements = [name for a in addresses for name, _, _ in settlements(norm(a))]

    qual = get(v, "qualification", "")
    qual_low = norm(qual).lower().strip() if isinstance(qual, str) else ""
    qual_flag = "with" if qual_low == "с опытом" else "without" if qual_low == "без опыта" else ""

    cands: list[Candidate] = []
    addr_stems = {stem(n) for n in addr_settlements}
    for s in [title_sent] + sents:
        if s.field != "job-name":
            cands.extend(_pay_candidates(s))
            if EXP_WORD.search(s.text):
                cands.append(Candidate("exp", s, experience_duration(s.text)))
            if REFERENCE.search(s.text):
                cands.append(Candidate("reference", s))
        if s.field != "schedule" and SCHEDULE_WORD.search(s.text):
            info = schedule_info(s.text)
            if info.has_params or info.regime or info.night:
                cands.append(Candidate("schedule", s, None, info))
        if PART_CUE.search(s.text):
            cands.append(Candidate("part", s))
        if s.field != "job-name" and PAY_UNIT.search(s.text):
            cands.append(Candidate("unit", s))
        if s.field != "job-name" and TEMP_CUE.search(s.text):
            cands.append(Candidate("temp", s))
        if s.field != "job-name" and EXTRA_TIME.search(s.text):
            cands.append(Candidate("extra", s))
        if REMOTE_CUE.search(s.text):
            cands.append(Candidate("remote", s))
        if ONSITE_CUE.search(s.text):
            cands.append(Candidate("onsite", s))
        for name, _, _ in settlements(s.text):
            cands.append(Candidate("settlement", s, name, {"same": stem(name) in addr_stems}))

    # Пары предложений из разных полей об одном требовании: в одном «обязательно», в другом «не требуется».
    for i, a in enumerate(sents):
        for b in sents[i + 1:]:
            if a.field == b.field:
                continue
            for rx in _REQ_TOPICS:
                if rx.search(a.text) and rx.search(b.text):
                    neg_a, neg_b = bool(_REQ_NEG.search(a.text)), bool(_REQ_NEG.search(b.text))
                    if neg_a != neg_b and _REQ_POS.search(b.text if neg_a else a.text):
                        cands.append(Candidate("req_pair", a, b))
                        break

    exp_raw = get(v, "requirement.experience")
    return Facts(
        vacancy=v,
        vid=str(get(v, "id", "") or ""),
        url=str(get(v, "vac_url", "") or ""),
        title=title,
        sentences=sents,
        salary_min=as_int(get(v, "salary_min")),
        salary_max=as_int(get(v, "salary_max")),
        schedule_raw=schedule_raw,
        schedule=sched,
        shift_values=shift_values,
        employment=get(v, "employment", "") if isinstance(get(v, "employment", ""), str) else "",
        education_raw=str(get(v, "requirement.education", "") or ""),
        education=education_level(get(v, "requirement.education")),
        experience=as_int(exp_raw),
        experience_present=exp_raw is not None and str(exp_raw).strip() != "",
        qualification_flag=qual_flag,
        addresses=addresses,
        address_settlements=addr_settlements,
        candidates=cands,
    )
