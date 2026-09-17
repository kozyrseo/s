#!/usr/bin/env python3
"""
KOZYR — детерминированный расчёт kozyr_score (0–10) из полей анкеты партнёра.

ФИЛОСОФИЯ НАДЁЖНОСТИ (v3, 2026):
  На покерном рынке безопасность ДЕНЕГ игрока определяется не лицензией и даже
  не софтом, а МОДЕЛЬЮ работы:
    • Рум (direct) — деньги напрямую с оператором, своя платформа. Безопаснее.
    • Клуб (через агента) — надстройка над чужим софтом (ClubGG/PPPoker),
      расчёты идут через ЧЕЛОВЕКА-агента. Софт может быть топовым, но деньги —
      у посредника без лицензии. Объективно рискованнее прямого рума.
    • Крипто/Telegram без KYC — быстро и анонимно, но свои риски.
  Лицензия — умеренный бонус, её отсутствие НЕ штрафуется (топ-румы бывают без
  неё). Наказываем только за реальные красные флаги (жалобы/невыплаты/скам).

Балл = сумма очков качества по критериям игрока: надёжность (модель+инфра),
рейкбек, выплаты, трафик/живость, игры/лимиты, платформы, платежи, бонусы,
мягкость полей. RAW → FINAL = CALIB_BASE + CALIB_SCALE*RAW (под якоря).

Возвращает (score, breakdown) с очками, максимумами, метками и пояснениями.
Отсутствующее поле = нейтральный вклад. Итог зажат в [SCORE_MIN, SCORE_MAX].
"""
from __future__ import annotations

# ── Калибровка (под якоря) ─────────────────────────────────────────────────
CALIB_BASE  = 6.56
CALIB_SCALE = 0.30
SCORE_MIN   = 5.5
SCORE_MAX   = 9.4

# ═══ НАДЁЖНОСТЬ v3: модель работы (рум vs клуб-агент) — ключевой фактор ═══
# База по МОДЕЛИ (безопасность денег), не по типу вообще.
TRUST_BASE_ROOM_DIRECT = 0.95   # рум, прямые расчёты — деньги с оператором
TRUST_BASE_ROOM_CRYPTO = 0.70   # крипто/telegram рум (быстро, но анонимно/без KYC)
TRUST_BASE_CLUB_AGENT  = 0.45   # клуб через агента — деньги через человека-посредника

# Инфраструктура/софт — вторичный сигнал (софт надёжен ≠ деньги надёжны).
TRUST_NET_TOP     = 0.45   # топовая проверенная сеть/платформа
TRUST_NET_KNOWN   = 0.28   # известная помельче
TRUST_NET_OWN     = 0.12   # собственный/безымянный движок

TOP_ROOM_NETWORKS = {
    "ggnetwork", "ggpoker", "gg", "wpn", "winning", "winning poker network",
    "ipoker", "pokerstars", "stars", "888", "888poker", "pacific",
    "chico", "chico poker network", "winamax", "partypoker", "party",
    "horizon", "boss", "microgaming",
}
TOP_CLUB_PLATFORMS = {
    "clubgg", "club gg", "pppoker", "ppp", "pokerbros", "bros",
    "upoker", "x-poker", "xpoker", "suprema", "pokerrrr", "pokerrrr2",
    "coinpoker",
}
KNOWN_NETWORKS = {
    "redstar", "red star", "betonline", "sportsbetting", "americas cardroom",
    "acr", "blackchip", "true poker", "pokerking", "ya poker", "yapoker",
    "pokerdom", "poker dom", "tigergaming",
}

# Лицензия — умеренный бонус, отсутствие БЕЗ штрафа.
TRUST_LIC_STRICT = 0.25
TRUST_LIC_BASIC  = 0.12
STRICT_LICENSE_KEYWORDS = ["mga", "malta", "ukgc", "gambling commission",
                           "kahnawake", "isle of man", "gibraltar", "alderney"]
BASIC_LICENSE_KEYWORDS  = ["лиценз", "licen", "curac", "curaç", "anjouan",
                           "gaming authority", "gaming board", "gaming license"]
LIC_NEGATIONS = ["без лиценз", "нет лиценз", "no licen", "без отдельной"]

# Репутация/присутствие на рынке — проверяемые сигналы доверия.
TRUST_REP_PTS = 0.20
REP_KEYWORDS  = ["проверенн", "надёжн", "надежн", "с 20", "years", "лет на рынке",
                 "давно", "репутац", "trusted", "established", "reliable",
                 "быстрые выплат", "мгновенн", "instant payout", "присутств",
                 "на рынке", "известн", "популярн"]

# KYC — плюс к безопасности (защита от мультиаккаунта/ботов, но минус анонимности).
TRUST_KYC_PTS = 0.10
KYC_KEYWORDS  = ["kyc", "верификац", "верифікац", "проверка личности", "id проверка"]
KYC_NEGATIONS = ["без kyc", "kyc нет", "нет kyc", "no kyc", "без верификац", "анонимн"]

# Красные флаги — единственный серьёзный минус.
TRUST_REDFLAG_PEN = 0.70
REDFLAG_KEYWORDS  = ["не плат", "не выплач", "скам", "scam", "кидал", "кидок",
                     "развод", "задержк выплат", "задержива выплат", "мошенн",
                     "не выводит", "проблемы с выводом", "чёрный список",
                     "black list", "blacklist", "жалобы на вывод", "не отдаёт",
                     "rip off", "ripoff", "fraud"]

TRUST_MAX = 1.85
TRUST_MIN = 0.15

# Красные флаги ОБРУШИВАЮТ весь балл (скам не должен спасаться рейкбеком).
# Флаг найден → финальный score зажимается сверху этим потолком.
REDFLAG_SCORE_CAP = 4.5   # рум с жалобами/скамом не выше 4.5, чем бы ни манил

# ── Рейкбек ───────────────────────────────────────────────────────────────
RAKE_MAX_PTS   = 1.50
RAKE_FULL_AT   = 50.0
RAKE_NONE_PTS  = 0.70

# ── Выплаты ────────────────────────────────────────────────────────────────
PAYOUT_TIERS = [(1, 1.25), (2, 1.05), (6, 0.85), (24, 0.60), (48, 0.35)]
PAYOUT_SLOW_PTS    = 0.15
PAYOUT_UNKNOWN_PTS = 0.45

# ── Трафик / живость (НОВЫЙ критерий v3) ────────────────────────────────────
# Мёртвые столы = бесполезный рум. Важный фактор для игрока.
TRAFFIC_MAX_PTS     = 1.00
TRAFFIC_HIGH_PTS    = 1.00   # много игроков / живые столы 24/7
TRAFFIC_MID_PTS     = 0.60   # средний трафик
TRAFFIC_LOW_PTS     = 0.25   # мало игроков / мёртвые столы днём
TRAFFIC_UNKNOWN_PTS = 0.50   # не указан → нейтрально
TRAFFIC_HIGH_KW = ["высок", "много игрок", "живые стол", "живой трафик", "24/7",
                   "круглосуточ", "тысяч онлайн", "плотн", "высокий трафик",
                   "high traffic", "always games", "packed"]
TRAFFIC_LOW_KW  = ["мало игр", "мало игрок", "низк трафик", "низкий трафик",
                   "мёртв", "мертв", "пусто", "слабый трафик", "мало столов",
                   "low traffic", "dead", "empty", "тонкий трафик"]
# Числовой трафик (примерное число онлайн)
TRAFFIC_NUM_HIGH = 1000
TRAFFIC_NUM_MID  = 200

# ── Игры/лимиты ────────────────────────────────────────────────────────────
GAMES_MAX_PTS     = 0.75
GAME_PT_EACH      = 0.18
LIMITS_WIDE_AT    = 5
LIMITS_WIDE_BONUS = 0.20

# ── Платформы ──────────────────────────────────────────────────────────────
PLATFORM_MAX_PTS = 0.60
PLATFORM_PT_EACH = 0.15
HUD_BONUS        = 0.10
KNOWN_PLATFORMS  = {"ios", "android", "win", "mac", "web"}

# ── Платежи ────────────────────────────────────────────────────────────────
PAY_MAX_PTS    = 0.50
PAY_PT_EACH    = 0.16
KNOWN_PAYMENTS = {"card", "bank", "crypto", "ewallet"}

# ── Бонусы ─────────────────────────────────────────────────────────────────
BONUS_MAX_PTS = 0.55
BONUS_PT_EACH = 0.18

# ── Мягкость поля ──────────────────────────────────────────────────────────
SOFT_MAX_PTS = 0.30
SOFT_PEN     = 0.10


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _network_tokens(draft: dict) -> str:
    return " ".join([str(draft.get("network") or ""),
                     str(draft.get("networkLabel") or "")]).lower()


def _access_model(draft: dict) -> str:
    """Определяет модель работы: room_direct / room_crypto / club_agent."""
    typ = (draft.get("type") or "room").strip().lower()
    access = (draft.get("access") or "").strip().lower()
    net = _network_tokens(draft)
    lic = (draft.get("license") or "").lower()
    # Клуб или доступ через агента/клуб
    if typ == "club" or access in ("club", "agent"):
        return "club_agent"
    # Крипто/Telegram рум (анонимный, часто без KYC)
    if access in ("telegram", "crypto") or "telegram" in net or \
       any(k in lic for k in ["без kyc", "kyc нет", "нет kyc", "no kyc"]):
        return "room_crypto"
    return "room_direct"


def _trust_points(draft: dict):
    """Надёжность v3: модель работы — ключевой фактор. (очки, пояснения)."""
    notes = []
    model = _access_model(draft)
    typ = (draft.get("type") or "room").strip().lower()

    # 1. База по МОДЕЛИ (безопасность денег)
    if model == "club_agent":
        pts = TRUST_BASE_CLUB_AGENT
        notes.append("клуб через агента (деньги через посредника)")
    elif model == "room_crypto":
        pts = TRUST_BASE_ROOM_CRYPTO
        notes.append("крипто/Telegram-рум")
    else:
        pts = TRUST_BASE_ROOM_DIRECT
        notes.append("прямой рум (расчёты с оператором)")

    # 2. Инфраструктура/софт — вторичный сигнал
    net = _network_tokens(draft)
    net_pts = TRUST_NET_OWN
    net_label = "свой движок"
    if typ == "club":
        if any(k in net for k in TOP_CLUB_PLATFORMS):
            net_pts, net_label = TRUST_NET_TOP, "надёжный софт"
        elif any(k in net for k in KNOWN_NETWORKS):
            net_pts, net_label = TRUST_NET_KNOWN, "известный софт"
    else:
        if any(k in net for k in TOP_ROOM_NETWORKS):
            net_pts, net_label = TRUST_NET_TOP, "топовая сеть"
        elif any(k in net for k in KNOWN_NETWORKS):
            net_pts, net_label = TRUST_NET_KNOWN, "известная сеть"
    pts += net_pts
    if net_pts >= TRUST_NET_KNOWN:
        notes.append(net_label)

    # 3. Лицензия — умеренный бонус
    lic = (draft.get("license") or "").lower()
    if any(k in lic for k in STRICT_LICENSE_KEYWORDS):
        pts += TRUST_LIC_STRICT; notes.append("строгая лицензия")
    elif any(k in lic for k in BASIC_LICENSE_KEYWORDS):
        if not any(neg in lic for neg in LIC_NEGATIONS):
            pts += TRUST_LIC_BASIC; notes.append("лицензия")

    # 4. Репутация/присутствие
    about = draft.get("about")
    about_s = " ".join(about) if isinstance(about, list) else str(about or "")
    rep_text = " ".join([str(draft.get("note") or ""),
                         " ".join(draft.get("pros") or []),
                         str(draft.get("payoutLabel") or ""), about_s]).lower()
    if any(k in rep_text for k in REP_KEYWORDS):
        pts += TRUST_REP_PTS; notes.append("присутствие/репутация")

    # 5. KYC — плюс к безопасности (если есть и не отрицается)
    kyc_text = " ".join([lic, rep_text]).lower()
    if any(k in kyc_text for k in KYC_KEYWORDS) and not any(n in kyc_text for n in KYC_NEGATIONS):
        pts += TRUST_KYC_PTS; notes.append("есть KYC")

    # 6. Красные флаги — серьёзный минус
    flag_text = " ".join([rep_text, str(draft.get("traffic") or ""),
                          " ".join(draft.get("cons") or [])]).lower()
    if any(k in flag_text for k in REDFLAG_KEYWORDS):
        pts -= TRUST_REDFLAG_PEN
        notes.append("⚠️ красные флаги")

    pts = round(max(TRUST_MIN, min(TRUST_MAX, pts)), 3)
    return pts, notes


def _rake_points(draft: dict) -> float:
    rake = draft.get("rake", "none")
    if rake in ("none", None, "", "null"):
        return RAKE_NONE_PTS
    val = _num(rake)
    if val is None:
        return RAKE_NONE_PTS
    return round(min(val, RAKE_FULL_AT) / RAKE_FULL_AT * RAKE_MAX_PTS, 3)


def _payout_points(draft: dict) -> float:
    hours = _num(draft.get("payoutHours"))
    if hours is None:
        return PAYOUT_UNKNOWN_PTS
    for max_h, pts in PAYOUT_TIERS:
        if hours <= max_h:
            return pts
    return PAYOUT_SLOW_PTS


def _traffic_points(draft: dict) -> float:
    """Трафик/живость столов. Число (traffic_online) или текст (traffic/note)."""
    # 1. Числовой трафик, если задан
    num = _num(draft.get("traffic_online"))
    if num is not None:
        if num >= TRAFFIC_NUM_HIGH:
            return TRAFFIC_HIGH_PTS
        if num >= TRAFFIC_NUM_MID:
            return TRAFFIC_MID_PTS
        return TRAFFIC_LOW_PTS
    # 2. Текстовые сигналы
    text = " ".join([str(draft.get("traffic") or ""),
                     str(draft.get("note") or ""),
                     " ".join(draft.get("pros") or []),
                     " ".join(draft.get("cons") or [])]).lower()
    high = any(k in text for k in TRAFFIC_HIGH_KW)
    low = any(k in text for k in TRAFFIC_LOW_KW)
    if high and not low:
        return TRAFFIC_HIGH_PTS
    if low and not high:
        return TRAFFIC_LOW_PTS
    if high and low:
        return TRAFFIC_MID_PTS   # смешанно (напр. «вечером живо, днём мало»)
    return TRAFFIC_UNKNOWN_PTS


def _games_points(draft: dict) -> float:
    games, limits = draft.get("games") or [], draft.get("limits") or []
    pts = min(len(games) * GAME_PT_EACH, GAMES_MAX_PTS - LIMITS_WIDE_BONUS)
    if len(limits) >= LIMITS_WIDE_AT:
        pts += LIMITS_WIDE_BONUS
    return round(min(pts, GAMES_MAX_PTS), 3)


def _platform_points(draft: dict) -> float:
    sw = [s.lower() for s in (draft.get("software") or [])]
    real = [s for s in sw if s in KNOWN_PLATFORMS]
    pts = min(len(real) * PLATFORM_PT_EACH, PLATFORM_MAX_PTS - HUD_BONUS)
    if "hud" in sw:
        pts += HUD_BONUS
    return round(min(pts, PLATFORM_MAX_PTS), 3)


def _payment_points(draft: dict) -> float:
    pays = [p.lower() for p in (draft.get("payments") or [])]
    real = [p for p in pays if p in KNOWN_PAYMENTS]
    return round(min(len(real) * PAY_PT_EACH, PAY_MAX_PTS), 3)


def _bonus_points(draft: dict) -> float:
    return round(min(len(draft.get("bonus") or []) * BONUS_PT_EACH, BONUS_MAX_PTS), 3)


def _softness_points(draft: dict) -> float:
    text = " ".join([str(draft.get("traffic") or ""),
                     " ".join(draft.get("pros") or []),
                     str(draft.get("note") or "")]).lower()
    soft = any(k in text for k in ["мягк", "любител", "рекреац", "soft", "fish"])
    hard = any(k in text for k in ["жёстк", "жестк", "регуляр", "grind", "tough"])
    if soft and not hard:
        return SOFT_MAX_PTS
    if hard and not soft:
        return -SOFT_PEN
    return 0.0


COMPONENT_LABELS = {
    "trust": "Надёжность", "rake": "Рейкбек", "payout": "Скорость выплат",
    "traffic": "Трафик и живость", "games": "Игры и лимиты",
    "platform": "Платформы и софт", "payments": "Платёжки",
    "bonus": "Бонусы", "softness": "Мягкость поля",
}
COMPONENT_MAX = {
    "trust": TRUST_MAX, "rake": RAKE_MAX_PTS, "payout": PAYOUT_TIERS[0][1],
    "traffic": TRAFFIC_MAX_PTS, "games": GAMES_MAX_PTS, "platform": PLATFORM_MAX_PTS,
    "payments": PAY_MAX_PTS, "bonus": BONUS_MAX_PTS, "softness": SOFT_MAX_PTS,
}


def _has_redflags(draft: dict) -> bool:
    """Есть ли красные флаги (жалобы/невыплаты/скам) в данных партнёра."""
    text = " ".join([
        str(draft.get("note") or ""),
        " ".join(draft.get("pros") or []),
        " ".join(draft.get("cons") or []),
        str(draft.get("traffic") or ""),
        str(draft.get("payoutLabel") or ""),
    ]).lower()
    return any(k in text for k in REDFLAG_KEYWORDS)


def compute_kozyr_score(draft: dict):
    """Возвращает (score, breakdown)."""
    trust_pts, trust_notes = _trust_points(draft)
    parts = {
        "trust":    trust_pts,
        "rake":     _rake_points(draft),
        "payout":   _payout_points(draft),
        "traffic":  _traffic_points(draft),
        "games":    _games_points(draft),
        "platform": _platform_points(draft),
        "payments": _payment_points(draft),
        "bonus":    _bonus_points(draft),
        "softness": _softness_points(draft),
    }
    raw = round(sum(parts.values()), 3)
    score = CALIB_BASE + CALIB_SCALE * raw
    # Красные флаги обрушивают балл: скам/невыплаты → не выше REDFLAG_SCORE_CAP,
    # и разрешаем упасть НИЖЕ обычного пола (плохой рум должен быть плохим).
    redflag = _has_redflags(draft)
    if redflag:
        score = min(score, REDFLAG_SCORE_CAP)
        score = round(max(1.0, min(SCORE_MAX, score)), 1)
    else:
        score = round(max(SCORE_MIN, min(SCORE_MAX, score)), 1)
    parts["_redflag"] = redflag
    parts["_raw"] = raw
    parts["_final"] = score
    parts["_trust_notes"] = trust_notes
    parts["_labels"] = COMPONENT_LABELS
    parts["_max"] = COMPONENT_MAX
    return score, parts


if __name__ == "__main__":
    import json, sys
    draft = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    s, br = compute_kozyr_score(draft)
    print(f"kozyr_score = {s}")
    for k, v in br.items():
        if not k.startswith("_"):
            lbl = COMPONENT_LABELS.get(k, k)
            print(f"  {lbl:>18}: {v:+.2f}")
    if br.get("_trust_notes"):
        print(f"  надёжность: {', '.join(br['_trust_notes'])}")
    print(f"  RAW={br['_raw']}  →  FINAL={br['_final']}")
