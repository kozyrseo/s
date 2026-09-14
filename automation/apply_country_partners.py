"""
apply_country_partners.py — проставляет код новой страны в markets выбранных
партнёров (мультигео, шаг workflow newcountry.yml).

Читает задание country_jobs/{code}.json, для каждого выбранного партнёра
добавляет код страны в его markets (partners.json). Так партнёрский pipeline,
который читает markets, будет знать, что партнёр раскатан на эту страну.

Идемпотентно: если страна уже в markets — пропускает.
"""
from __future__ import annotations
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from partner_markets import add_market_to_partner  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="Код страны")
    ap.add_argument("--job", required=True, help="Путь к country_jobs/{code}.json")
    args = ap.parse_args()

    job_path = Path(args.job)
    if not job_path.exists():
        print(f"⚠️  Задание не найдено: {job_path}")
        return 0

    job = json.loads(job_path.read_text(encoding="utf-8"))
    partner_ids = job.get("partners", [])
    code = args.code.strip().lower()

    if not partner_ids:
        print("Партнёры не выбраны — нечего помечать.")
        return 0

    added = 0
    for pid in partner_ids:
        if add_market_to_partner(pid, code):
            print(f"  ✅ {pid}: + рынок {code}")
            added += 1
        else:
            print(f"  ℹ️ {pid}: рынок {code} уже был (или партнёр не найден)")

    print(f"Готово: {added} партнёров помечено рынком {code}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
