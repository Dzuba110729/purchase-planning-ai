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
document.addEventListener("click", event => {
  const button = event.target.closest("[data-card]");
  if (!button) return;
  const template = document.getElementById("card-" + button.dataset.card);
  if (template) {
    productBody.replaceChildren(template.content.cloneNode(true));
    dialog.showModal();
    dialog.scrollTop = 0;
  }
});
dialog.addEventListener("close", () => productBody.replaceChildren());
firstPage();
