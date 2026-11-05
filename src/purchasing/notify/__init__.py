"""Send plain-text Telegram notifications to the single configured chat."""

import http.client
import json
import os
import re
import urllib.request

from .published import PublishedPlan, read_public_plan

__all__ = ["NotificationError", "read_public_plan", "send_failure", "send_summary"]

_FAILURES = {
    "data": "Проверьте формат и обязательные поля CSV. Подробности в шаге пересчёта GitHub Actions.",
    "fetch": "Проверьте доступ на чтение и комплект CSV в закрытом репозитории.",
    "build": "Проверьте шаг сборки в GitHub Actions.",
}


class NotificationError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise NotificationError("Проверьте настройку TELEGRAM_BOT_TOKEN.")
    return token


def _send(text: str, chat: str | None = None) -> None:
    token = _token()
    chat = (chat or os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
    if not re.fullmatch(r"-?[1-9][0-9]{0,15}", chat):
        raise NotificationError("Проверьте настройку TELEGRAM_CHAT_ID.")
    chat_id = int(chat)
    body = json.dumps({
        "chat_id": chat_id, "text": text,
        "link_preview_options": {"is_disabled": True},
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            result = json.loads(response.read(256_001))
            if result["ok"] is not True or result["result"]["chat"]["id"] != chat_id:
                raise ValueError
    except (OSError, KeyError, TypeError, ValueError, RecursionError, http.client.HTTPException):
        raise NotificationError("Telegram не подтвердил доставку. Проверьте настройки бота и чата.") from None


def _changes(plan, previous):
    current = plan.positions
    old = previous.positions
    changed = []
    for sku in sorted(current.keys() | old.keys()):
        new, prior = current.get(sku), old.get(sku)
        new_order = (new.qty, new.date) if new and new.qty else (0, None)
        old_order = (prior.qty, prior.date) if prior and prior.qty else (0, None)
        if new_order != old_order:
            if new_order[0] == old_order[0]:
                change = f"{old_order[1] or 'без даты'} → {new_order[1] or 'без даты'}"
            else:
                change = f"{old_order[0]} → {new_order[0]} шт."
            changed.append(f"{sku} {change}")
    new_shortages = [sku for sku, item in current.items() if item.shortage and (sku not in old or not old[sku].shortage)]
    new_overstock = [sku for sku, item in current.items() if item.overstock and (sku not in old or not old[sku].overstock)]
    qty_change = plan.totals["order_qty_total"] - previous.totals["order_qty_total"]
    if not (changed or new_shortages or new_overstock or qty_change or any(
        plan.totals[k] != previous.totals[k] for k in ("order_now_items", "shortage_risk_items", "overstock_risk_items")
    )):
        return ["Что изменилось: рекомендации и риски прежние."]
    lines = [f"Что изменилось: объём к заказу {qty_change:+d} шт. Изменённых рекомендаций: {len(changed)}."]
    if changed:
        lines.append(", ".join(changed[:3]))
    if new_shortages:
        lines.append(f"Новый риск дефицита: {len(new_shortages)} поз. ({', '.join(new_shortages[:3])}).")
    if new_overstock:
        lines.append(f"Новый риск излишка: {len(new_overstock)} поз. ({', '.join(new_overstock[:3])}).")
    for field, label in (("shortage_risk_items", "дефицит"), ("overstock_risk_items", "излишек")):
        delta = plan.totals[field] - previous.totals[field]
        if delta:
            lines.append(f"Изменение числа позиций, {label}: {delta:+d}.")
    return lines


def send_summary(plan: PublishedPlan, previous: PublishedPlan | None, pages_url: str, *, first=False) -> None:
    totals = plan.totals
    lines = [
        f"План закупок на {plan.date}",
        f"К заказу по плану: {totals['order_qty_total']} шт. Разместить сейчас: {totals['order_now_items']} поз.",
    ]
    orders = [f"{sku} × {item.qty} шт." for sku, item in plan.positions.items() if item.now and item.qty]
    if orders:
        lines.append(", ".join(orders[:3]))
    lines.append(
        f"Риск дефицита: {totals['shortage_risk_items']} поз. Риск излишка: {totals['overstock_risk_items']} поз."
    )
    shortages = [sku for sku, item in plan.positions.items() if item.shortage]
    if shortages:
        lines.append(f"Под риском дефицита: {', '.join(shortages[:3])}.")
    if previous is not None:
        lines.extend(_changes(plan, previous))
    elif first:
        lines.append("Что изменилось: предыдущая публикация отсутствует.")
    else:
        lines.append("Что изменилось: предыдущая публикация недоступна для сравнения.")
    if plan.templates:
        lines.append("ИИ недоступен для части или всех пояснений. Использованы шаблоны.")
    lines.append(pages_url)
    _send("\n".join(lines))


def send_failure(reason: str, pages_url: str) -> None:
    _send(f"Пересчёт закупок остановлен. {_FAILURES[reason]}\nДашборд не обновлён.\n{pages_url}")
