# Защита от утечки данных и ключей

Репозиторий открытый, поэтому данные заказчика (CSV) и ключи в него попасть не должны. Защита в несколько слоёв:

1. **`.gitignore`** — игнорирует `*.csv`, `share/`, `.env`, `.env.*` (кроме `.env.example`).
2. **pre-commit** — хук `block-data-files` падает на любом `.csv` (в любом регистре), файле из `share/`, `.env` и `.env.*`; хук `gitleaks` ищет ключи и токены в добавляемых изменениях. Включить: `uv run pre-commit install` (нужен установленный `gitleaks`).
3. **Автотест** `tests/test_repo_guards.py` — `git ls-files` не должен содержать CSV, `share/` и `.env`; `.env.example` — только имена переменных.
4. **CI** (`.github/workflows/ci.yml`) — на каждый push и pull request: тесты, проверка отсутствия CSV в git, gitleaks по всей истории.
5. **Секреты** — только в секретах GitHub (Settings → Secrets and variables → Actions) и в локальном `.env`. В коде, тестах, логах и чате ключей нет.

## Включить push protection на GitHub (делает владелец репозитория)

Settings → Code security → Secret protection → Push protection → Enable.

После этого GitHub сам блокирует push, в котором найден ключ. Включите также «Secret scanning» в том же разделе.

## Если ключ всё же попал в git

Считайте его скомпрометированным: отзовите и выпустите новый. Удаление из истории ключ не спасает.
