"use strict";

const filters = document.getElementById("filters");
const search = document.getElementById("sku-filter");
const category = document.getElementById("category-filter");
const mode = document.getElementById("mode-filter");
const table = document.getElementById("recommendation-table");
const rows = Array.from(table?.querySelectorAll("tbody tr") ?? []);
const empty = document.getElementById("filter-empty");
const pagination = document.getElementById("pagination");
const previous = document.getElementById("previous");
const next = document.getElementById("next");
const pageStatus = document.getElementById("page-status");
const pageSize = 12;
let page = 0;

function applyFilters() {
  if (!table) return;
  const code = search.value.trim().toUpperCase();
  const matched = rows.filter(row =>
    row.dataset.sku.includes(code) &&
    (!category.value || row.dataset.category === category.value) &&
    (mode.value === "all" || row.dataset.modes.split(" ").includes(mode.value))
  );
  rows.forEach(row => { row.hidden = true; });
  matched.slice(page * pageSize, (page + 1) * pageSize).forEach(row => { row.hidden = false; });
  const lastPage = Math.ceil(matched.length / pageSize);
  previous.disabled = page === 0;
  next.disabled = page + 1 >= lastPage;
  pageStatus.textContent = `Страница ${page + 1} из ${lastPage}`;
  pagination.hidden = lastPage < 2;
  empty.hidden = matched.length > 0;
  table.hidden = matched.length === 0;
}

function firstPage() {
  page = 0;
  applyFilters();
}

filters.addEventListener("input", firstPage);
filters.addEventListener("submit", event => event.preventDefault());
filters.addEventListener("reset", () => setTimeout(firstPage, 0));
previous.addEventListener("click", () => {
  page--;
  applyFilters();
});
next.addEventListener("click", () => {
  page++;
  applyFilters();
});
document.querySelectorAll("[data-risk]").forEach(link => {
  link.addEventListener("click", () => {
    search.value = "";
    category.value = "";
    mode.value = link.dataset.risk;
    firstPage();
  });
});
document.querySelector('a[href="#supplier-review"]').addEventListener("click", () => {
  document.getElementById("supplier-review").open = true;
});

const dialog = document.getElementById("product");
const productBody = document.getElementById("product-body");
const SVG_NS = "http://www.w3.org/2000/svg";

// Карточка товара собирается здесь из CARD_DATA (данные подготовил Python; всё — через textContent и setAttribute).
function make(namespace, tag, attrs = {}, text) {
  const element = namespace ? document.createElementNS(namespace, tag) : document.createElement(tag);
  Object.entries(attrs).forEach(([name, value]) => element.setAttribute(name, value));
  if (text !== undefined) element.textContent = text;
  return element;
}

function factSpan(key, value, source) {
  return make(null, "span", { "data-fact": key, "data-value": value ? value[0] : "", title: source },
    value ? value[1] : CARD_DATA.nodata);
}

function forecastChart(sku, points) {
  if (!points.length) return make(null, "p", { class: "empty" }, "Помесячный прогноз: в данных нет.");
  const peak = Math.max(...points.map(point => parseFloat(point[1])), 1);
  const coords = points.map((point, index) => [
    32 + 536 * index / Math.max(points.length - 1, 1),
    112 - 93 * parseFloat(point[1]) / peak,
  ]);
  const figure = make(null, "figure", { class: "chart", title: CARD_DATA.monthly_source });
  const caption = make(null, "figcaption", {}, "Прогноз спроса по месяцам ");
  caption.append(make(null, "span", {}, "шт. за месяц"));
  const svg = make(SVG_NS, "svg", {
    viewBox: "0 0 600 144", role: "img", "aria-label": "Помесячный прогноз спроса, штук за месяц",
  });
  svg.append(make(SVG_NS, "path", { class: "gridlines", d: "M32 20H568 M32 66H568 M32 112H568" }));
  svg.append(make(SVG_NS, "polyline", { points: coords.map(([x, y]) => x.toFixed(1) + "," + y.toFixed(1)).join(" ") }));
  const labels = make(SVG_NS, "g", { class: "chart-label", title: "facts.json → items[].monthly_forecast" });
  points.forEach(([month, raw, shown], index) => {
    const [x, y] = coords[index];
    const circle = make(SVG_NS, "circle", {
      cx: x.toFixed(1), cy: y.toFixed(1), r: "4", "data-fact": `items.${sku}.monthly_forecast.${month}`,
      "data-value": raw, title: "facts.json → items[].monthly_forecast",
    });
    circle.append(make(SVG_NS, "title", {}, `${month} · ${shown} шт.`));
    svg.append(circle);
    if (index === 0 || index === points.length - 1) {
      labels.append(make(SVG_NS, "text", { x: x.toFixed(1), y: "135" }, `${month.slice(5, 7)}.${month.slice(2, 4)}`));
    }
  });
  svg.append(labels);
  figure.append(caption, svg);
  return figure;
}

function buildCard(sku, [categoryLabel, statusCodes, values, points, note]) {
  const body = document.createDocumentFragment();
  body.append(make(null, "p", { class: "eyebrow" }, categoryLabel), make(null, "h2", { id: "product-title" }, sku));
  const status = make(null, "p", { class: "status-line" });
  if (statusCodes.length) {
    statusCodes.forEach((code, index) => {
      if (index) status.append(" ");
      const [tone, label] = CARD_DATA.tags[code];
      status.append(make(null, "span", { class: tone ? "tag " + tone : "tag" }, label));
    });
  } else {
    status.textContent = "—";
  }
  body.append(status, forecastChart(sku, points));
  const facts = make(null, "dl", { class: "facts-grid" });
  CARD_DATA.fields.forEach(([field, label, source], index) => {
    const row = make(null, "div");
    const value = make(null, "dd");
    value.append(factSpan(`items.${sku}.${field}`, values[index], source));
    row.append(make(null, "dt", {}, label), value);
    facts.append(row);
  });
  body.append(facts);
  if (note) {
    const [isAi, text, tooltip] = note;
    const box = make(null, "div", { class: "note" });
    box.append(
      make(null, "span", { class: "provenance" }, isAi ? "Пояснение ИИ, цифры проверены" : "Шаблон"),
      make(null, "p", { title: CARD_DATA.tooltips[tooltip] }, text),
    );
    body.append(box);
  }
  return body;
}

document.addEventListener("click", event => {
  const button = event.target.closest("[data-card]");
  if (!button) return;
  const card = CARD_DATA.cards[button.dataset.card];
  if (card) {
    productBody.replaceChildren(buildCard(button.dataset.card, card));
    dialog.showModal();
    dialog.scrollTop = 0;
  }
});
dialog.addEventListener("close", () => productBody.replaceChildren());
firstPage();
