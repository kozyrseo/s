"""
partner_markets.py — работа с партнёрами по рынкам (странам).

Часть мультигео-модели. Используется генератором страны и Telegram-мастером
(Этап 3) для ответа на вопрос «каких партнёров предложить для страны X».

Модель:
  • partner["markets"] = список стран (кодов), где партнёр РАСКАТАН/годится.
    Это ЯВНЫЙ выбор оператора, не автоматика. Бот предлагает партнёра для
    страны, только если она есть в markets.
  • partner["countries"] = где страница партнёра УЖЕ создана (факт раскатки).
    markets ⊇ countries: партнёр может быть «годен» для страны, но ещё не
    раскатан там.
"""
from __future__ import annotations
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARTNERS_JSON = REPO_ROOT / "partners.json"


def _load_partners(partners_path: Path | None = None) -> list[dict]:
    path = partners_path or PARTNERS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("partners", [])


def partners_for_market(country_code: str,
                        partners_path: Path | None = None) -> list[dict]:
    """Партнёры, годные для страны (country_code в markets).

    Возвращает список кратких карточек для показа в мастере:
      {id, name, type, currency, already_here}
    already_here = страница партнёра для этой страны уже существует
    (country в countries) — мастер может пометить «уже раскатан».
    """
    result = []
    for p in _load_partners(partners_path):
        markets = p.get("markets", [])
        if country_code in markets:
            existing = p.get("countries") or ([p["country"]] if p.get("country") else [])
            result.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "type": p.get("type"),
                "currency": p.get("currency"),
                "already_here": country_code in existing,
            })
    return result


def add_market_to_partner(partner_id: str, country_code: str,
                          partners_path: Path | None = None) -> bool:
    """Добавляет страну в markets партнёра (для мастера: «раскатать сюда»).

    Возвращает True если добавлено, False если партнёр не найден или страна
    уже была в markets. Пишет обратно в partners.json.
    """
    path = partners_path or PARTNERS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    for p in data.get("partners", []):
        if p.get("id") == partner_id:
            markets = p.setdefault("markets", [])
            if country_code in markets:
                return False
            markets.append(country_code)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return True
    return False


def all_partners_brief(partners_path: Path | None = None) -> list[dict]:
    """Все партнёры кратко — для мастера, чтобы показать полный список и дать
    оператору выбрать, кому добавить новый рынок."""
    result = []
    for p in _load_partners(partners_path):
        result.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "type": p.get("type"),
            "currency": p.get("currency"),
            "markets": list(p.get("markets", [])),
        })
    return result


def rollout_partner_to_market(partner_id: str, country_code: str, *,
                              currency: str, card_rows: list,
                              note: str = "", partners_path: Path | None = None) -> bool:
    """Раскатывает партнёра на новый рынок: создаёт byMarket[country].

    МУЛЬТИГЕО: пишет в partners.json блок byMarket[country] с валютой, путём,
    карточкой (rows) и note для этой страны. НЕ трогает украинские (плоские)
    поля и byMarket других стран. Также добавляет страну в markets.

    Аргументы:
      currency   — валюта партнёра на этом рынке (из мастера)
      card_rows  — строки карточки [[label, value, hi], ...] под страну
                   (суммы уже в местной валюте — их задаёт оператор/Claude)
      note       — краткое описание партнёра на языке страны

    Возвращает True если раскатан, False если партнёр не найден.
    """
    path = partners_path or PARTNERS_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    for p in data.get("partners", []):
        if p.get("id") != partner_id:
            continue
        # определяем тип (room/club) для пути
        kind = "clubs" if p.get("type") == "club" else "rooms"
        url = f"/{country_code}/{kind}/{partner_id}/"

        # logoImg — общий (лого не зависит от страны, пункт 6а)
        base_card = p.get("card", {})
        market_card = {
            "logoImg": base_card.get("logoImg", ""),   # тот же логотип
            "kind": base_card.get("kind", p.get("name", "")),
            "dark": base_card.get("dark", False),
            "rows": card_rows,
        }

        p.setdefault("byMarket", {})
        p["byMarket"][country_code] = {
            "currency": currency,
            "url": url,
            "card": market_card,
            "note": note or base_card.get("note", ""),
        }
        # добавляем страну в markets (если ещё нет)
        markets = p.setdefault("markets", [])
        if country_code not in markets:
            markets.append(country_code)
        # добавляем в countries (факт раскатки)
        countries = p.setdefault("countries", [])
        if country_code not in countries:
            countries.append(country_code)

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    return False
