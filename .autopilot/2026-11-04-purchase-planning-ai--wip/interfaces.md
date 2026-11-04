# Границы, решённые в спецификации

Ориентир для тасков 1–9. Имена — английские. Детали внутри модуля решает исполнитель своего таска, но границы ниже не меняются без записи `D##`.

## Модули (пакет `src/purchasing/`)

| Модуль | Владеет | Отдаёт наружу | Прячет |
|---|---|---|---|
| `data` (таск 2) | чтение 6 CSV, проверка формата, восстановление нулевых остатков, разметка | `load_dataset(data_dir) -> Dataset`, `DataError` (текст «в файле X нет колонки Y»), `quality_report(dataset)` | детали pandas, форматы дат |
| `forecast` (таск 3) | упущенный спрос, возвраты, сезонный профиль, прогноз, бэктест | `build_forecast(dataset, plan_date) -> Forecast`, `backtest(dataset) -> BacktestResult` | подбор параметров |
| `planning` (таск 4) | плечо, покрытие, страховой запас, количество, даты, флаги, `facts.json` | `build_plan(dataset, forecast, plan_date) -> PurchasePlan`, `write_facts(plan, path)` | формулы запаса |
| `ai` (таск 5) | паспорт фактов, вызов DeepSeek, проверка ответов, запасная цепочка, журнал | `write_texts(facts) -> Texts` (всегда возвращает тексты, даже при сбое API) | HTTP, ключи, промпты |
| `dashboard` (таск 6) | сборка статичной страницы и Excel | `build_site(facts, texts, out_dir)`, `build_xlsx(facts, path)` | HTML-шаблоны, графики |
| `notify` (таск 7) | Telegram-сводка | `send_summary(facts, texts, changes)` | токен, формат сообщения |
| `cli` | команды запуска | `uv run purchasing run` (весь прогон), отдельные шаги | |

Проверенные данные идут вперёд по цепочке `data → forecast → planning → ai → dashboard → notify`. Назад ничего не течёт. Цифры считают только `data`, `forecast`, `planning`; `ai`, `dashboard`, `notify` цифры не меняют (R05, R06, R76).

## Швы для тестов
- Файлы CSV во временной папке (`tmp_path`) — вход `data`.
- Подмена HTTP-ответа DeepSeek (без интернета) — вход `ai`.
- Готовый `facts.json` — вход `dashboard` и `notify`.

## Правила проекта
- Стек: Python 3.12, `uv`, pandas, pytest. Версии фиксирует `uv.lock`.
- Установка: `uv sync`. Одна проверка всего: `uv run pytest` (таск 1 создаёт её). Один файл: `uv run pytest tests/<файл>.py`.
- В git не попадают: `*.csv`, `share/`, `.env`, журналы (`.gitignore` уже есть).
- Ключи, токены, пароли не пишутся ни в файлы, ни в чат, ни в логи. В репозитории — только пустые имена в `.env.example`.
- Любое число в тексте для человека (README, сводка, сценарий) берётся из `facts.json`.
- Не переделывать прошлые этапы без причины; свой этап закрывает только зелёный набор его проверок.

## Что построили закрытые таски
(пока ничего)

### Таск 03 (forecast)
- `build_forecast(dataset, plan_date=None, params=DEFAULT_PARAMS, horizon=18) -> Forecast(monthly[sku_id,category,month,qty], profile[категория×1..12], level, adjusted_history, lost_months; for_sku(), sum_months())`
- `backtest(dataset, plan_date=2025-08-13, params) -> BacktestResult(table, detail, wape(cat,"model"|"base"), better())`; `fit()`, `Params`, `format_backtest()`, `forecast.plots.plot_backtest()`
- CLI: `uv run purchasing backtest [--data-dir] [--plots-dir]`

### Таск 04 (planning)
- `build_plan(dataset, forecast, plan_date=None, accuracy=None) -> PurchasePlan` (`items`, `lists`, `totals`, `forecast_accuracy`, `params`; `.item(sku)`, `.to_facts()`); `write_facts(plan, path) -> Path`; `FACTS_SCHEMA_VERSION = 1`.
- CLI: `uv run purchasing facts [--data-dir] [--plan-date] [--out out/facts.json]` (ошибка данных → код 2). `out/` и `facts.json` в `.gitignore`.
- `facts.json`, верхний уровень: `schema_version, plan_date, plan_date_source, generated_from{sku_rows,sales_rows,stock_rows,order_rows,receipt_rows,supplier_rows}, params{...}, totals, items[], lists, forecast_accuracy`.
- `totals`: live_total, dead_total, auto_purchase_items, order_qty_total, order_now_items, shortage_risk_items, shortage_risk_auto_items, overstock_risk_items, overstock_risk_auto_items, price_review_items, price_review_ending_items, sell_off_items, sell_off_qty, ask_supplier_items, ask_supplier_qty.
- `items[]` (по каждому живому товару): sku_id, category, supplier_id|null, supply_type, markup_pct, abc_group, importance, in_auto_purchase, exclusion_reason, stock, in_transit_qty, overdue_included_qty, overdue_excluded_qty, daily_demand, stock_days(+_note), runout_days, runout_date, lead_time_days(+source,+note), cover_days(+source,+note), demand_lead_cover, service_level, safety_stock, safety_note, safety_capped, target_stock, batch_step, batch_step_source, batch_step_note, order_qty|null(+_note), next_order_date|null(+_note), order_now, shortage_risk, unavoidable_shortage, overstock_risk, flags[], notes[], missing[]. Нет данных = null + пометка, число не выдумывается.
- `lists`: price_review[{sku_id,category,markup_pct,stock,stock_days,ending}], sell_off[{sku_id,category,stock}], ask_supplier[{sku_id,po_id|null,supplier_id|null,qty,days_overdue,promised_date}].
- `forecast_accuracy`: {warning, computed, backtest_plan_date, horizon_months, categories[{category,skus,fact_units,wape_model,wape_base,model_better}]}.
- Правка плеча (T04): плечо = медиана `planned_lead_days` заказов поставщика (≥3, значения ≤0 и >400 отброшены), иначе медиана категории (unknown — сразу категория), иначе null «нет данных». Новый верхний ключ `lead_time_distribution{items,orders_planned,by_source,by_supply_type,by_category}` — в каждом n, p25, median, p90. σ спроса — по продажам минус возвраты; «мало истории» — меньше 3 мес. от первой продажи.

### Таск 05 (ai)

- `write_texts(facts, *, config=AIConfig(), journal_path=...) -> Texts` принимает словарь из `facts.json` и возвращает `summary`, `anomalies`, `items` по ключу `sku_id`, `requests`, `token_usage`, `unaccounted_requests`, `errors`. Входные факты остаются неизменными.
- `Text(text, used_facts, insufficient_data, source, model)`. Для отображения использовать `text` как обычный текст и показывать происхождение из `source` (`ai` или `template`). Числа закупки и даты брать из `facts.json`.
- DeepSeek выбирает и упорядочивает фразы из вариантов, построенных из паспорта. Код сверяет фразы целиком, проверяет объединение ссылок на факты и согласованность флага отсутствия данных. Шаблон строится из тех же вариантов.
- `python -m purchasing.ai.acceptance [--facts out/facts.json] [--off-peak]` требует `DEEPSEEK_API_KEY` в окружении процесса. Отчёт с текстами и журнал запросов остаются в закрытой папке `out/ai-private/`. Оценка стоимости использует тариф пиковых часов по умолчанию. `--off-peak` применяет тариф вне пика.

### Таск 06 (dashboard)

- `build_site(facts, texts, out_dir) -> Path` записывает самодостаточный `index.html` со ссылкой на `order.xlsx`. `build_xlsx(facts, path) -> Path` записывает книгу со строками заказа и сводкой.
- `uv run purchasing dashboard [--facts out/facts.json] [--out out/dashboard]` создаёт оба файла с шаблонными пояснениями без запросов к API. Для проверенных пояснений ИИ вызывающая сторона передаёт `Texts` в `build_site`.
- В `facts.json` версии 1 добавлены `items[].monthly_forecast[{month,qty}]`, `lists.discontinue[{sku_id,category,stock}]` и счётчики `totals.weight_known_items`, `open_order_items`, `overdue_order_items`, `overdue_order_qty`. Дашборд читает и прежний формат.

- Правка «шаг партии» (после T06, без автопилота): шаг — кратность, до которой округляем заказ ВВЕРХ, а не размер прошлой партии. Шаг по истории (НОД заказов товара) берётся, только если заказов в истории ≥ 3 и шаг ≤ 50% цели запаса; иначе — лесенка по цели запаса: < 300 → 10, < 3000 → 50, дальше 100 (`batch_step_source = "scale"`, вместо прежнего `"default"`). Минимальную партию поставщика не придумываем: если самый маленький прошлый заказ товара больше рассчитанного заказа в 3+ раза, у позиции флаг `possible_min_batch` и пояснение в `batch_step_note` («возможна минимальная партия у поставщика, проверьте»). Параметры — в `params.batch_step_ladder`, `min_orders_for_batch_step`, `history_step_max_share`, `min_batch_flag_ratio`. Потребность до округления = `max(target_stock − stock − in_transit_qty, 0)` (`in_transit_qty` уже включает просроченное до 90 дней).
- Пороги проверок на реальных данных (`tests/planning/test_planning_real_data.py`), подобраны по данным: сумма заказа ≤ 1,3 × суммарной потребности (получилось 1,05; до правки 2,6); заказ больше потребности в 3+ раза допустим только если перебор ≤ 10 шт. (округление крошечной потребности до шага 10) либо стоит флаг `possible_min_batch`.
- `items[].monthly_forecast[].qty` округлён до 2 знаков (меньше размер файла и страницы).
- Дашборд: карточки товаров собираются в браузере из `CARD_DATA` (JSON внутри единственного `<script>`), а не лежат в странице готовыми `<template>`. Вес страницы на реальных данных ≈ 1,5 МБ (было 6,3). Подписи списков: «Вывести из ассортимента (нет продаж 12 мес.)» — все мёртвые (`dead_total`), «Распродать остаток» — только с остатком (`sell_off_items`, `sell_off_qty`).
