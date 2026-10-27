"""Harcama sınırı ve API kullanım kaydı.

Her ücretli çağrıdan ÖNCE `ensure_budget()` çağrılır; günlük sınır aşılacaksa
BudgetExceeded fırlatılır ve çağrı yapılmaz. Çağrı sonrası `record()` ile
gerçekleşen (tahmini) maliyet kaydedilir.
"""
from . import db


class BudgetExceeded(Exception):
    pass


def spent_today() -> float:
    row = db.one("SELECT COALESCE(SUM(cost_usd),0) AS s FROM api_usage "
                 "WHERE created_at >= date_trunc('day', now())")
    return float(row["s"])


def ensure_budget(estimated_cost: float) -> None:
    limit = float(db.get_setting("daily_cost_limit_usd", 5.0))
    spent = spent_today()
    if spent + estimated_cost > limit:
        raise BudgetExceeded(
            f"Günlük maliyet sınırı: harcanan {spent:.3f}$ + tahmini {estimated_cost:.3f}$ > sınır {limit:.2f}$")


def record(provider: str, operation: str, cost: float, success: bool, video_id=None,
           units=None, unit_type=None) -> None:
    db.exec_("INSERT INTO api_usage (provider, operation, video_id, units, unit_type, cost_usd, success) "
             "VALUES (%s,%s,%s,%s,%s,%s,%s)", (provider, operation, video_id, units, unit_type, cost, success))
    if video_id and cost:
        db.exec_("UPDATE videos SET cost_usd = cost_usd + %s WHERE id=%s", (cost, video_id))
