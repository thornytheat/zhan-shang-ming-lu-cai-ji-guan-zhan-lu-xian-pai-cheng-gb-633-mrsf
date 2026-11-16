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
  bindWithdraw();
}

/* 撤回清除：主体可选名称或编号，再加可选的展位号定位。
   请求体与 /api/redactions 一致，成功后刷新回执页。 */
function withdrawBody() {
  const subject = document.getElementById("subject");
  const booth = document.getElementById("booth");
  const text = subject ? subject.value.trim() : "";
  if (!text) {
    throw new Error("请填展商名称或编号");
  }
  const body = /^E[0-9a-f]{6,}$/i.test(text) ? { ex_id: text } : { name: text };
  const boothText = booth ? booth.value.trim() : "";
  if (boothText) {
    body.booth = boothText;
  }
  return body;
}

async function withdraw() {
  if (state.busy) {
    return;
  }
  state.busy = true;
  let data;
  try {
    data = await post("/api/redactions", withdrawBody());
  } catch (error) {
    state.busy = false;
    setWithdrawMessage(error.message, true);
    return;
  }
  state.busy = false;
  setWithdrawMessage(
    "回执 " + data.receipt_id + " · " + data.state_label +
      " · 命中 " + data.summary.matched_rows + " 行",
    false
  );
  window.location.href = "/redactions?receipt_id=" + data.receipt_id;
}

async function resumeWithdraw(button) {
  if (state.busy) {
    return;
  }
  state.busy = true;
  try {
    const data = await post(
      "/api/redactions/" + button.dataset.receipt + "/resume",
      {}
    );
    setWithdrawMessage("回执 " + data.receipt_id + " · " + data.state_label, false);
    window.location.href = "/redactions?receipt_id=" + data.receipt_id;
  } catch (error) {
    setWithdrawMessage(error.message, true);
  } finally {
    state.busy = false;
  }
}

function setWithdrawMessage(text, bad) {
  const node = document.getElementById("withdrawmsg");
  if (!node) {
    return;
  }
  node.textContent = text;
  node.classList.toggle("err", Boolean(bad));
}

function bindWithdraw() {
  const button = document.getElementById("withdraw");
  if (button) {
    button.addEventListener("click", withdraw);
  }
  const resumeButton = document.getElementById("resume");
  if (resumeButton) {
    resumeButton.addEventListener("click", () => resumeWithdraw(resumeButton));
  }
}

document.addEventListener("DOMContentLoaded", bind);
