/* table.js — 表头点击排序：只改变展示顺序，不动后端数据 */
const sortState = {
  table: null,
  column: -1,
  ascending: true,
};

function valueOf(cell) {
  const raw = cell.dataset.sort;
  const text = (raw === undefined ? cell.textContent : raw).trim();
  const number = Number(text.replace(/,/g, ""));
  if (text !== "" && !Number.isNaN(number)) {
    return { kind: "num", value: number };
  }
  return { kind: "text", value: text };
}

function sortBy(table, column) {
  const body = table.querySelector("tbody");
  if (!body) {
    return;
  }
  const rows = Array.from(body.querySelectorAll("tr")).filter(
    (row) => row.querySelectorAll("td").length > column
  );
  if (!rows.length) {
    return;
  }
  const same = sortState.table === table && sortState.column === column;
  sortState.table = table;
  sortState.column = column;
  sortState.ascending = same ? !sortState.ascending : true;
  const sign = sortState.ascending ? 1 : -1;
  rows.sort((left, right) => {
    const a = valueOf(left.children[column]);
    const b = valueOf(right.children[column]);
    if (a.kind === "num" && b.kind === "num") {
      return (a.value - b.value) * sign;
    }
    return String(a.value).localeCompare(String(b.value), "zh-Hans-CN") * sign;
  });
  rows.forEach((row) => body.appendChild(row));
  mark(table, column, sortState.ascending);
}

function mark(table, column, ascending) {
  table.querySelectorAll("thead th").forEach((node) => {
    node.removeAttribute("aria-sort");
    node.classList.remove("is-sorted");
  });
  const head = table.querySelectorAll("thead th")[column];
  if (head) {
    head.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    head.classList.add("is-sorted");
  }
}

function bind() {
  document.querySelectorAll("table.grid").forEach((table) => {
    const heads = table.querySelectorAll("thead th");
    heads.forEach((head, index) => {
      if (index === 0) {
        return;
      }
      head.style.cursor = "pointer";
      head.addEventListener("click", () => sortBy(table, index));
    });
  });
}

document.addEventListener("DOMContentLoaded", bind);
