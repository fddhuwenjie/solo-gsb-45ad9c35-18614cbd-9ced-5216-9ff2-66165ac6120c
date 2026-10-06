/* 公共工具：HTML 转义、fetch JSON、提示条/toast。 */

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function api(method, url, body) {
  const opts = { method, headers: { "Accept": "application/json" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  if (!resp.ok) {
    const err = new Error((data && data.error) || `请求失败（${resp.status}）`);
    err.status = resp.status;
    err.code = data && data.code;
    err.details = data && data.details;
    err.currentRevision = data && data.details && data.details.current;
    throw err;
  }
  return data;
}

let toastTimer = null;
function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function showBanner(container, kind, message) {
  container.hidden = false;
  container.className = `banner ${kind}`;
  container.innerHTML = message;
}
function hideBanner(container) {
  container.hidden = true;
  container.innerHTML = "";
}
