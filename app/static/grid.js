/* grid.js — 单元格键盘导航：方向键移动焦点，Home/End 跳到行首行尾 */
const gridState = {
  tables: [],
};

function cellsOf(table) {
  return Array.from(table.querySelectorAll("td.cell[tabindex]"));
}

function locate(cells, node) {
  const index = cells.indexOf(node);
  if (index < 0) {
    return null;
  }
  const row = node.parentElement;
  const table = row.closest("table");
  const rows = Array.from(table.querySelectorAll("tbody tr")).filter(
    (item) => item.querySelector("td.cell[tabindex]")
  );
  return { rowIndex: rows.indexOf(row), colIndex: Array.from(row.children).indexOf(node), rows };
}

function focusCell(table, rowIndex, colIndex) {
  const rows = Array.from(table.querySelectorAll("tbody tr")).filter(
    (item) => item.querySelector("td.cell[tabindex]")
  );
  if (rowIndex < 0 || rowIndex >= rows.length) {
    return false;
  }
  const row = rows[rowIndex];
  const cells = Array.from(row.children).filter((node) => node.classList.contains("cell"));
  if (!cells.length) {
    return false;
  }
  const target = cells[Math.max(0, Math.min(colIndex, cells.length - 1))];
  if (target) {
    target.focus();
    return true;
  }
  return false;
}

function onKey(event) {
  const node = event.target;
  if (!node || !node.classList || !node.classList.contains("cell")) {
    return;
  }
  const table = node.closest("table");
  if (!table) {
    return;
  }
  const cells = cellsOf(table);
  const spot = locate(cells, node);
  if (!spot) {
    return;
  }
  let moved = false;
  if (event.key === "ArrowDown" || event.key === "j") {
    moved = focusCell(table, spot.rowIndex + 1, spot.colIndex);
  } else if (event.key === "ArrowUp" || event.key === "k") {
    moved = focusCell(table, spot.rowIndex - 1, spot.colIndex);
  } else if (event.key === "ArrowRight" || event.key === "l") {
    moved = focusCell(table, spot.rowIndex, spot.colIndex + 1);
  } else if (event.key === "ArrowLeft" || event.key === "h") {
    moved = focusCell(table, spot.rowIndex, spot.colIndex - 1);
  } else if (event.key === "Home") {
    moved = focusCell(table, spot.rowIndex, 0);
  } else if (event.key === "End") {
    moved = focusCell(table, spot.rowIndex, 99);
  }
  if (moved) {
    event.preventDefault();
  }
}

function bind() {
  gridState.tables = Array.from(document.querySelectorAll("table.grid"));
  document.addEventListener("keydown", onKey);
}

document.addEventListener("DOMContentLoaded", bind);
