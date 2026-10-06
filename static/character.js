/* 角色页：本角色台词 + 紧前一句提示 + 笔记（发布版只读）。 */

const panel = document.querySelector(".panel[data-character]");
const character = panel.dataset.character;
const frozen = panel.dataset.frozen === "true";
const dataUrl = panel.dataset.url;
const listEl = document.getElementById("cue-list");
const banner = document.getElementById("banner");
const revBadge = document.getElementById("rev-badge");

let data = null;

function render() {
  revBadge.textContent = frozen
    ? `冻结于 R${data.revision}`
    : `工作稿 R${data.revision}`;

  if (!data.lines.length) {
    listEl.innerHTML = '<p class="sub">这一版里没有该角色的台词。</p>';
    return;
  }

  listEl.innerHTML = data.lines.map((line) => {
    let cueHtml;
    if (line.cue) {
      cueHtml = `<div class="cue-line"><span class="cue-tag">提示</span>`
        + `<strong>${escapeHtml(line.cue.character)}</strong>：${escapeHtml(line.cue.text)}</div>`;
    } else {
      cueHtml = '<div class="cue-line no-cue"><span class="cue-tag">提示</span>（这是全稿第一句，没有前一句）</div>';
    }

    let noteHtml;
    if (frozen) {
      noteHtml = line.note
        ? `<div class="frozen-note"><span class="note-label">笔记（冻结）</span>${escapeHtml(line.note)}</div>`
        : '<div class="frozen-note note-label">（无笔记）</div>';
    } else {
      noteHtml = `
        <div class="note-area">
          <label for="note-${line.id}">我的笔记（始终跟随台词 #${line.id}，重排改词都不会丢）</label>
          <textarea id="note-${line.id}" rows="2" maxlength="5000"
                    placeholder="给这句台词写提示、调度、情绪记号…">${escapeHtml(line.note)}</textarea>
          <div class="note-actions">
            <button type="button" class="btn small primary" data-action="save-note" data-id="${line.id}">保存笔记</button>
            <button type="button" class="btn small" data-action="clear-note" data-id="${line.id}">清空笔记</button>
            <span class="note-status" id="note-status-${line.id}"></span>
          </div>
        </div>`;
    }

    return `
      <article class="cue-item${frozen ? " frozen" : ""}">
        ${cueHtml}
        <div class="my-line"><span class="line-id">#${line.id}</span>${escapeHtml(line.text)}</div>
        ${noteHtml}
      </article>`;
  }).join("");
}

async function load() {
  data = await api("GET", dataUrl);
  render();
}

async function saveNote(lineId, content) {
  const status = document.getElementById(`note-status-${lineId}`);
  try {
    const resp = await api("PUT", `/api/lines/${lineId}/note`, {
      content,
      base_revision: data.revision,
    });
    const line = data.lines.find((l) => l.id === lineId);
    if (line) line.note = content.trim();
    data.revision = resp.revision;
    revBadge.textContent = `工作稿 R${data.revision}`;
    if (status) status.textContent = "已保存";
    hideBanner(banner);
    toast("笔记已保存，修订号已更新到 R" + resp.revision);
  } catch (err) {
    if (err.code === "stale_revision") {
      showBanner(
        banner, "error",
        `⚠️ ${escapeHtml(err.message)}<br>你刚输入的笔记还在输入框里，没有被覆盖。`
        + `点 <strong>重新载入最新稿</strong> 后再保存即可。 `
        + `<button class="btn small" type="button" id="btn-reload">重新载入最新稿</button>`,
      );
      document.getElementById("btn-reload").addEventListener("click", async () => {
        await load();
        hideBanner(banner);
      });
    } else {
      showBanner(banner, "error", "保存失败：" + escapeHtml(err.message));
    }
  }
}

listEl.addEventListener("click", (ev) => {
  if (frozen) return;
  const btn = ev.target.closest("button[data-action]");
  if (!btn) return;
  const lineId = Number(btn.dataset.id);
  const textarea = document.getElementById(`note-${lineId}`);
  if (btn.dataset.action === "save-note") {
    saveNote(lineId, textarea.value);
  } else if (btn.dataset.action === "clear-note") {
    textarea.value = "";
    saveNote(lineId, "");
  }
});

load().catch((err) => {
  showBanner(banner, "error", "载入失败：" + escapeHtml(err.message));
});
