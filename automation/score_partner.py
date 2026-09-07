#!/usr/bin/env python3
"""
KOZYR — детерминированный расчёт kozyr_score (0–10) из полей анкеты партнёра.

Балл собирается из объективных характеристик, важных игроку: доверие/лицензия,
рейкбек, скорость выплат, разнообразие игр и лимитов, охват платформ, платежи,
бонусы, мягкость полей. Каждый компонент даёт «очки качества», их сумма (RAW,
обычно ~4.0–5.5) переводится в отображаемую шкалу аффинным преобразованием
FINAL = CALIB_BASE + CALIB_SCALE * RAW. Две калибровочные константы подобраны
так, чтобы существующие партнёры совпали со своими оценками:
    PokerBet (RAW 5.52) → 8.2      KlubOk (RAW 4.54) → 7.9
Меняешь редполитику — крутишь веса компонентов; хочешь сдвинуть всю шкалу —
крутишь CALIB_BASE/CALIB_SCALE.

Функция возвращает (score, breakdown) — разбивку, чтобы было видно, ПОЧЕМУ
вышло столько. Отсутствующее поле = нейтральный вклад, а не ноль (не наказываем
за неполную анкету). Итог зажимается в [SCORE_MIN, SCORE_MAX].
"""
from __future__ import annotations

# ── Перевод суммы очков качества в шкалу 0–10 (подогнано под якоря) ────────
CALIB_BASE  = 6.55        # сдвиг всей шкалы
CALIB_SCALE = 0.30        # множитель суммы очков качества
SCORE_MIN   = 5.5         # ниже не опускаем
SCORE_MAX   = 9.4         # 9.5+ бережём для исключительных, вручную

# ── Доверие: тип площадки + лицензия (самый весомый блок) ─────────────────
TRUST_ROOM_LICENSED = 1.75   # рум с реальной лицензией (Curaçao/MGA/…)
TRUST_ROOM_OFFSHORE = 1.05   # рум без отдельной лицензии / офшор
TRUST_CLUB_UNION    = 0.85   # клуб в известном союзе/юнионе
TRUST_CLUB_PLAIN    = 0.60   # клуб без союза
# ВАЖНО: negation-ключи проверяются ПЕРВЫМИ, иначе «без лицензии» ловится как
# «лиценз» и даёт ложный плюс. Офшор/отсутствие лицензии → не licensed.
OFFSHORE_KEYWORDS = ["офшор", "offshore", "без лиценз", "нет лиценз",
                     "без отдельной", "клубн", "no licen", "without licen"]
LICENSE_KEYWORDS  = ["лиценз", "licen", "curac", "curaç", "mga", "kahnawake",
                     "gaming authority", "gaming board", "anjouan"]

# ── Рейкбек (0..RAKE_MAX_PTS) ─────────────────────────────────────────────
RAKE_MAX_PTS   = 1.50
RAKE_FULL_AT   = 50.0     # % при котором даём максимум (выше — тот же максимум)
RAKE_NONE_PTS  = 0.70     # «нет рейкбека» ≠ 0: ценность может быть в бонусах

# ── Скорость выплат (0..PAYOUT_MAX_PTS), по payoutHours ───────────────────
PAYOUT_TIERS = [(1, 1.25), (2, 1.05), (6, 0.85), (24, 0.60), (48, 0.35)]
PAYOUT_SLOW_PTS    = 0.15   # дольше 48ч
PAYOUT_UNKNOWN_PTS = 0.45   # payoutHours не указан → нейтрально

# ── Разнообразие игр и лимитов (0..GAMES_MAX_PTS) ─────────────────────────
GAMES_MAX_PTS     = 0.75
GAME_PT_EACH      = 0.18   # за каждый формат: cash/mtt/spins/sng
LIMITS_WIDE_AT    = 5      # столько лимитов = «широкая линейка»
LIMITS_WIDE_BONUS = 0.20

# ── Платформы (0..PLATFORM_MAX_PTS) ───────────────────────────────────────
PLATFORM_MAX_PTS = 0.60
PLATFORM_PT_EACH = 0.15    # ios/android/win/mac/web
HUD_BONUS        = 0.10    # поддержка HUD/трекеров — плюс для регуляров
KNOWN_PLATFORMS  = {"ios", "android", "win", "mac", "web"}

# ── Платежи (0..PAY_MAX_PTS) ──────────────────────────────────────────────
PAY_MAX_PTS    = 0.50
PAY_PT_EACH    = 0.16      # card/bank/crypto/ewallet
KNOWN_PAYMENTS = {"card", "bank", "crypto", "ewallet"}

# ── Бонусы/промо (0..BONUS_MAX_PTS) ───────────────────────────────────────
BONUS_MAX_PTS = 0.55
BONUS_PT_EACH = 0.18

# ── Мягкость полей ────────────────────────────────────────────────────────
SOFT_MAX_PTS = 0.30        # «мягкие поля / много любителей»
SOFT_PEN     = 0.10        # «жёсткие поля / регуляры» — небольшой минус


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _trust_points(draft: dict) -> float:
    typ = (draft.get("type") or "room").strip().lower()
    lic = (draft.get("license") or "").lower()
    offshore = any(k in lic for k in OFFSHORE_KEYWORDS)
    licensed = (not offshore) and any(k in lic for k in LICENSE_KEYWORDS)
    if typ == "room":
        return TRUST_ROOM_LICENSED if licensed else TRUST_ROOM_OFFSHORE
    return TRUST_CLUB_UNION if draft.get("union") else TRUST_CLUB_PLAIN


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


def compute_kozyr_score(draft: dict) -> tuple[float, dict]:
    """Возвращает (score, breakdown). score округлён до 0.1 и зажат в границы."""
    parts = {
        "trust":    _trust_points(draft),
        "rake":     _rake_points(draft),
        "payout":   _payout_points(draft),
        "games":    _games_points(draft),
        "platform": _platform_points(draft),
        "payments": _payment_points(draft),
        "bonus":    _bonus_points(draft),
        "softness": _softness_points(draft),
    }
    raw = round(sum(parts.values()), 3)
    score = round(max(SCORE_MIN, min(SCORE_MAX, CALIB_BASE + CALIB_SCALE * raw)), 1)
    parts["_raw"] = raw
    parts["_final"] = score
    return score, parts


if __name__ == "__main__":
    import json, sys
    draft = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else json.load(sys.stdin)
    s, br = compute_kozyr_score(draft)
    print(f"kozyr_score = {s}")
    for k, v in br.items():
        if not k.startswith("_"):
            print(f"  {k:>9}: {v:+.2f}")
    print(f"  RAW={br['_raw']}  →  FINAL={br['_final']}")
