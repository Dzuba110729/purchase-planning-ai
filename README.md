# Планирование закупок

Python рассчитывает рекомендации по данным о продажах, остатках и поставках. Статичный дашборд показывает план закупок, риски и пояснения, а Excel содержит строки к заказу.

## Локальный просмотр

```sh
uv sync
uv run purchasing facts
uv run purchasing dashboard
```

Расчёт читает данные из локальной папки `share/`. Если `out/facts.json` уже готов, достаточно последней команды. Она собирает `out/dashboard/index.html` и `out/dashboard/order.xlsx` с шаблонными пояснениями без обращения к DeepSeek. Откройте `index.html` в браузере и скачайте Excel со страницы.

```sh
uv run playwright install chromium
uv run pytest
```

Для проверки в установленном Google Chrome используйте `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome uv run pytest tests/dashboard`.

## Данные

Для демонстрации предусмотрены обезличенные коды товаров и расчётные показатели. CSV и полный `facts.json` хранятся локально. Для реальных данных компании потребуется доступ по почте, например через Cloudflare Access.
