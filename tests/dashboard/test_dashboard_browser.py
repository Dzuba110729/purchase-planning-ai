from dataclasses import replace
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from purchasing.ai import AIConfig, Text, write_texts
from purchasing.dashboard import build_site, build_xlsx


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        channel = os.getenv("PLAYWRIGHT_CHROMIUM_CHANNEL")
        if channel:
            instance = playwright.chromium.launch(channel=channel)
        else:
            if not Path(playwright.chromium.executable_path).is_file():
                pytest.skip("браузер Playwright: uv run playwright install chromium")
            instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def test_page(browser):
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.set_default_timeout(5000)
    errors = []
    dialogs = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

    def capture_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", capture_dialog)
    yield page
    assert not dialogs
    assert not errors
    page.close()


def test_filters_card_and_excel_download(test_page, sample_facts, tmp_path):
    texts = write_texts(sample_facts, config=AIConfig(max_requests=0))
    attack = "<script>alert(1)</script>"
    injected = Text(attack, (), False, "ai")
    texts = replace(texts, summary=injected, items={**texts.items, "SKU-000001": injected})
    path = build_site(sample_facts, texts, tmp_path)
    build_xlsx(sample_facts, tmp_path / "order.xlsx")

    test_page.goto(path.as_uri())
    expect(test_page.locator("#summary .note p")).to_have_text(attack)
    expect(test_page.locator("#summary .note script")).to_have_count(0)
    orders = test_page.locator("#recommendation-table tbody tr")
    expect(orders.locator("visible=true")).to_have_count(2)
    test_page.get_by_label("Категория", exact=True).select_option("grain_mills")
    expect(orders.locator("visible=true")).to_have_count(1)
    test_page.get_by_label("Код товара", exact=True).fill("not-found")
    expect(test_page.get_by_text("По этим фильтрам ничего не найдено.")).to_be_visible()
    test_page.get_by_role("button", name="Сбросить").click()
    expect(orders.locator("visible=true")).to_have_count(2)
    test_page.get_by_label("Рекомендация", exact=True).select_option("now")
    expect(orders.locator("visible=true")).to_have_count(1)

    test_page.locator('#recommendations [data-card="SKU-000001"]').click()
    product = test_page.get_by_role("dialog")
    expect(product.get_by_role("heading", name="SKU-000001")).to_be_visible()
    expect(product.get_by_role("img", name="Помесячный прогноз спроса, штук за месяц")).to_be_visible()
    expect(product.locator('[data-fact="items.SKU-000001.order_qty"]')).to_have_text("30")
    expect(product.locator(".note p")).to_have_text(attack)
    expect(product.locator(".note script")).to_have_count(0)
    product.get_by_role("button", name="Закрыть").click()
    expect(product).to_be_hidden()

    test_page.locator('[data-risk="overstock"]').click()
    expect(test_page.get_by_label("Рекомендация", exact=True)).to_have_value("overstock")
    expect(orders.locator("visible=true")).to_have_count(1)
    expect(test_page.locator('#recommendations [data-card="SKU-000002"]')).to_be_visible()
    with test_page.expect_download() as download:
        test_page.get_by_role("link", name="Скачать Excel").click()
    assert download.value.suggested_filename == "order.xlsx"


def test_real_data_at_375_px_including_expanded_sections_and_card(test_page, real_facts, tmp_path):
    texts = write_texts(real_facts, config=AIConfig(max_requests=0))
    path = build_site(real_facts, texts, tmp_path)
    test_page.set_viewport_size({"width": 375, "height": 812})
    test_page.emulate_media(color_scheme="dark")
    test_page.goto(path.as_uri())
    expect(test_page.get_by_role("heading", name="План закупок", exact=True)).to_be_visible()

    def assert_no_horizontal_scroll():
        assert test_page.evaluate("document.documentElement.scrollWidth === window.innerWidth")

    assert_no_horizontal_scroll()
    test_page.get_by_role("button", name="Далее", exact=True).click()
    expect(test_page.get_by_role("status")).to_contain_text("Страница 2 из")
    test_page.get_by_role("button", name="Назад", exact=True).click()
    expect(test_page.get_by_role("status")).to_contain_text("Страница 1 из")
    test_page.locator("#calendar summary").first.click()
    test_page.locator("#reviews summary").first.click()
    assert_no_horizontal_scroll()
    test_page.locator("#recommendation-table tr:not([hidden]) [data-card]").first.click()
    product = test_page.get_by_role("dialog")
    expect(product).to_be_visible()
    assert product.evaluate("(dialog) => dialog.scrollWidth === dialog.clientWidth")
    assert_no_horizontal_scroll()
    test_page.keyboard.press("Escape")
    expect(product).to_be_hidden()
