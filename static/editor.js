/* 编辑工作稿：增删 / 改派 / 改词 / 重排 / 发布。所有写操作都带所见修订号。 */

const listEl = document.getElementById("line-list");
const banner = document.getElementById("banner");
const revBadge = document.getElementById("rev-badge");
const addForm = document.getElementById("add-form");
const addCharacter = document.getElementById("add-character");
const addText = document.getElementById("add-text");
const addAfter = document.getElementById("add-after");
const characterList = document.getElementById("character-list");

let draft = null;

function revLabel() { return `工作稿 R${draft.revision}`; }

function render() {
  revBadge.textContent = revLabel();

  characterList.innerHTML = draft.characters
    .map((c) => `<option value="${escapeHtml(c)}"></option>`).join("");

  const afterValue = addAfter.value;
  addAfter.innerHTML = '<option value="">排到全稿末尾</option>'
    + draft.lines.map(
      (l) => `<option value="${l.id}">在 #${l.id}（${escapeHtml(l.character)}：${escapeHtml(truncate(l.text, 16))}）之后</option>`,
    ).join("");
  if ([...addAfter.options].some((o) => o.value === afterValue)) addAfter.value = afterValue;

  listEl.innerHTML = draft.lines.map((line, idx) => `
    <li class="line-row" draggable="true" data-id="${line.id}">
      <span class="drag-handle" title="拖动重排">⠿</span>
      <div class="line-main">
        <div class="view">
          <div class="line-meta">
            <span class="line-id">#${line.id}</span>
            <span class="line-character">${escapeHtml(line.character)}</span>
            ${line.note ? '<span class="note-flag">有笔记</span>' : ""}
          </div>
          <div class="line-text">${escapeHtml(line.text)}</div>
        </div>
        <div class="editors">
          <label>角色（改派到别人时，若该句有笔记会被整次拒绝）
            <input class="edit-character" maxlength="100" value="${escapeHtml(line.character)}">
          </label>
          <label>台词正文（改词不影响笔记，笔记按编号跟随）
            <textarea class="edit-text" rows="2" maxlength="5000">${escapeHtml(line.text)}</textarea>
          </label>
          <div class="row-actions">
            <button type="button" class="btn small primary" data-action="save" data-id="${line.id}">保存改动</button>
            <button type="button" class="btn small" data-action="cancel" data-id="${line.id}">取消</button>
          </div>
        </div>
      </div>
      <div class="row-actions">
        <button type="button" class="btn small" data-action="up" data-id="${line.id}" ${idx === 0 ? "disabled" : ""}>↑</button>
        <button type="button" class="btn small" data-action="down" data-id="${line.id}" ${idx === draft.lines.length - 1 ? "disabled" : ""}>↓</button>
        <button type="button" class="btn small" data-action="edit" data-id="${line.id}">改派/改词</button>
        <button type="button" class="btn small danger" data-action="delete" data-id="${line.id}">删除</button>
      </div>
    </li>`).join("");
}

function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

async function load() {
  draft = await api("GET", "/api/draft");
  render();
}

function staleMessage(err) {
  let extra = "";
  if (err.code === "notes_blocking" && Array.isArray(err.details) && err.details.length) {
    const items = err.details.map((n) =>
      `<li>台词 <a href="/character/${encodeURIComponent(n.character)}">#${n.line_id}</a>`
      + `（${escapeHtml(n.character)}）：${escapeHtml(n.content || "（有笔记内容）")}</li>`).join("");
    extra = `<div class="blocked-detail">必须先处理的笔记（到对应角色页清空后，本次操作才能重做）：<ul>${items}</ul></div>`;
  }
  return { text: err.message, extra };
}

function fail(err, prefix) {
  const info = staleMessage(err);
  showBanner(
    banner, "error",
    `⚠️ ${prefix ? prefix + "：" : ""}${escapeHtml(info.text)}${info.extra}`,
  );
}

async function mutate(fn, busyMsg) {
  hideBanner(banner);
  try {
    await fn();
  } catch (err) {
    fail(err, busyMsg);
    if (err.status === 409) {
      // 冲突后一定以服务端为准重新载入，避免继续用旧修订号提交
      await load();
    }
    return false;
  }
  return true;
}

async function sendReorder(ids) {
  const ok = await mutate(async () => {
    const resp = await api("POST", "/api/lines/reorder", {
      order: ids,
      base_revision: draft.revision,
    });
    if (resp.changed) {
      await load();
      toast("顺序已调整，提示将自动重算 · R" + resp.revision);
    }
  }, "重排失败");
  return ok;
}

function move(id, delta) {
  const ids = draft.lines.map((l) => l.id);
  const i = ids.indexOf(id);
  const j = i + delta;
  if (j < 0 || j >= ids.length) return;
  [ids[i], ids[j]] = [ids[j], ids[i]];
  sendReorder(ids);
}

async function saveEdit(id) {
  const row = listEl.querySelector(`.line-row[data-id="${id}"]`);
  const character = row.querySelector(".edit-character").value;
  const text = row.querySelector(".edit-text").value;
  const ok = await mutate(async () => {
    const resp = await api("PATCH", `/api/lines/${id}`, {
      character, text, base_revision: draft.revision,
    });
    await load();
    toast(resp.changed ? "改动已保存 · R" + resp.revision : "内容没有变化");
  }, "保存失败");
  if (!ok) return;
}

async function deleteLine(id) {
  const line = draft.lines.find((l) => l.id === id);
  if (!confirm(`确认删除台词 #${id}（${line.character}）？\n编号删除后永不再用；若它有笔记，服务器会整次拒绝。`)) return;
  await mutate(async () => {
    const resp = await api("DELETE", `/api/lines/${id}`, { base_revision: draft.revision });
    await load();
    toast("已删除，后续台词编号不变、顺序自动收紧 · R" + resp.revision);
  }, "删除失败");
}

listEl.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  switch (btn.dataset.action) {
    case "up": move(id, -1); break;
    case "down": move(id, 1); break;
    case "edit": listEl.querySelector(`.line-row[data-id="${id}"]`).classList.add("editing"); break;
    case "cancel":
      load().then(() => hideBanner(banner));
      break;
    case "save": saveEdit(id); break;
    case "delete": deleteLine(id); break;
  }
});

/* 拖拽重排 */
let dragId = null;
listEl.addEventListener("dragstart", (ev) => {
  const row = ev.target.closest(".line-row");
  if (!row) return;
  dragId = Number(row.dataset.id);
  row.classList.add("dragging");
  ev.dataTransfer.effectAllowed = "move";
  try { ev.dataTransfer.setData("text/plain", String(dragId)); } catch (e) { /* ignore */ }
});
listEl.addEventListener("dragover", (ev) => {
  const row = ev.target.closest(".line-row");
  if (!row || dragId === null) return;
  ev.preventDefault();
  listEl.querySelectorAll(".dragover").forEach((r) => r.classList.remove("dragover"));
  row.classList.add("dragover");
});
listEl.addEventListener("dragleave", (ev) => {
  const row = ev.target.closest(".line-row");
  if (row) row.classList.remove("dragover");
});
listEl.addEventListener("drop", (ev) => {
  const row = ev.target.closest(".line-row");
  if (!row || dragId === null) return;
  ev.preventDefault();
  const targetId = Number(row.dataset.id);
  const ids = draft.lines.map((l) => l.id);
  const from = ids.indexOf(dragId);
  ids.splice(from, 1);
  ids.splice(ids.indexOf(targetId), 0, dragId);
  dragId = null;
  sendReorder(ids);
});
document.addEventListener("dragend", () => {
  dragId = null;
  listEl.querySelectorAll(".dragging,.dragover")
    .forEach((r) => r.classList.remove("dragging", "dragover"));
});

/* 新增 */
addForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const afterRaw = addAfter.value;
  await mutate(async () => {
    const resp = await api("POST", "/api/lines", {
      character: addCharacter.value,
      text: addText.value,
      after_id: afterRaw === "" ? null : Number(afterRaw),
      base_revision: draft.revision,
    });
    await load();
    addText.value = "";
    toast(`已新增台词 #${resp.line_id} · R${resp.revision}`);
  }, "新增失败");
});

/* 发布：冻结全部角色页与笔记，工作稿修订号不变 */
document.getElementById("btn-publish").addEventListener("click", async () => {
  const label = prompt(
    `将把当前工作稿 R${draft.revision} 的全部角色页和笔记冻结为发布版。\n可填写版本备注（可留空）：`,
    `第 ${draft.revision} 版`,
  );
  if (label === null) return;
  try {
    const resp = await api("POST", "/api/publish", { label: label.trim() });
    showBanner(
      banner, "ok",
      `已发布 <a href="/r/${resp.revision}/">第 ${resp.revision} 版冻结快照</a>。`
      + "工作稿保持在 R" + draft.revision + "，可继续修改，不影响已发布版。",
    );
    toast("发布成功：R" + resp.revision);
  } catch (err) {
    fail(err, "发布失败");
  }
});

document.getElementById("btn-refresh").addEventListener("click", async () => {
  await load();
  hideBanner(banner);
  toast("已载入最新工作稿");
});

load().catch((err) => fail(err, "载入工作稿失败"));
