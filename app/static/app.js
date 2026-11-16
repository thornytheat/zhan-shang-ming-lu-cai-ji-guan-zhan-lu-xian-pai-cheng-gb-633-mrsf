/* app.js — 触发采集与重跑：状态集中在 state 里，失败给出可见反馈 */
const state = {
  busy: false,
  message: "",
};

function setMessage(text, bad) {
  const node = document.getElementById("runmsg");
  if (!node) {
    return;
  }
  node.textContent = text;
  node.classList.toggle("err", Boolean(bad));
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const text = await response.text();
  let data = null;
  try {
    data = JSON.parse(text);
  } catch (error) {
    data = { error: "E_PARSE", message: text.slice(0, 120) };
  }
  if (!response.ok) {
    throw new Error(data.message || "请求失败 " + response.status);
  }
  return data;
}

function sourceValue() {
  const node = document.getElementById("source");
  return node ? node.value : "all";
}

async function run(replay) {
  if (state.busy) {
    return;
  }
  state.busy = true;
  const buttons = [document.getElementById("run"), document.getElementById("replay")];
  buttons.forEach((node) => {
    if (node) {
      node.disabled = true;
    }
  });
  setMessage("运行中", false);
  try {
    const body = replay
      ? { source: sourceValue(), mode: "resume" }
      : { source: sourceValue(), mode: "full" };
    const data = await post("/api/crawl", body);
    if (data.status === "done") {
      setMessage(
        data.idempotent_replay
          ? "复用批次 " + data.replay_of
          : "批次 " + data.batch_id + " · 排入 " + total(data, "scheduled"),
        false
      );
      window.location.reload();
      return;
    }
    setMessage("停在 " + (data.failed_stage || "?") + "：" + (data.error || ""), true);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    state.busy = false;
    buttons.forEach((node) => {
      if (node) {
        node.disabled = false;
      }
    });
  }
}

function total(data, key) {
  const totals = data.totals || {};
  return totals[key] === undefined ? "—" : totals[key];
}

function bind() {
  const runButton = document.getElementById("run");
  const replayButton = document.getElementById("replay");
  if (runButton) {
    runButton.addEventListener("click", () => run(false));
  }
  if (replayButton) {
    replayButton.addEventListener("click", () => run(true));
  }
}

document.addEventListener("DOMContentLoaded", bind);
