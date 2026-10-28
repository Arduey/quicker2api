/* ═══════════ 通用小工具 ═══════════ */
(function () {
  "use strict";

  const CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  window.TB = {
    csrf: CSRF,

    /** 统一 POST（自动带 CSRF） */
    async post(url, data) {
      const body = data instanceof FormData ? data : new FormData();
      if (!(data instanceof FormData) && data) {
        Object.keys(data).forEach((k) => body.append(k, data[k]));
      }
      if (!body.has("_csrf")) body.append("_csrf", CSRF);
      const resp = await fetch(url, {
        method: "POST",
        body,
        headers: { "X-CSRF-Token": CSRF, Accept: "application/json" },
        credentials: "same-origin",
      });
      let json = null;
      try { json = await resp.json(); } catch (e) { json = { ok: false, message: "返回内容无法解析" }; }
      return json;
    },

    toast(message, kind) {
      const box = document.createElement("div");
      box.className = "alert " + (kind || "info");
      box.style.cssText =
        "position:fixed;top:18px;right:18px;z-index:9999;max-width:380px;box-shadow:var(--shadow-lg);margin:0";
      box.textContent = message;
      document.body.appendChild(box);
      setTimeout(() => {
        box.style.transition = "opacity .3s";
        box.style.opacity = "0";
        setTimeout(() => box.remove(), 320);
      }, 3800);
    },

    copy(text, okMessage) {
      const done = () => window.TB.toast(okMessage || "已复制到剪贴板", "ok");
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
      } else {
        fallbackCopy(text, done);
      }
    },

    /** 按钮进入 loading 态，返回恢复函数 */
    busy(btn, label) {
      if (!btn) return () => {};
      const original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = label || "处理中…";
      return () => { btn.disabled = false; btn.innerHTML = original; };
    },
  };

  function fallbackCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position:fixed;opacity:0;top:0;left:0";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy"); done(); } catch (e) { window.TB.toast("复制失败，请手动选择", "error"); }
    area.remove();
  }

  /* ── 复制按钮：data-copy="文本" 或按选择器取 ── */
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    ev.preventDefault();
    let value = btn.getAttribute("data-copy");
    if (btn.hasAttribute("data-copy-from")) {
      const el = document.querySelector(btn.getAttribute("data-copy-from"));
      if (el) value = "value" in el && el.value !== undefined && el.tagName !== "DIV" ? el.value : el.textContent;
    }
    TB.copy((value || "").trim(), btn.getAttribute("data-copy-message"));
  });

  /* ── 危险操作确认 ── */
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    const message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      ev.preventDefault();
      return;
    }
    // 双击防抖
    if (form.dataset.submitting === "1") { ev.preventDefault(); return; }
    form.dataset.submitting = "1";
    setTimeout(() => { form.dataset.submitting = ""; }, 4000);
  });

  /* ── 移动端侧栏 ── */
  const toggle = document.getElementById("menuToggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const shell = document.getElementById("shell");
      const sidebar = shell.querySelector(".sidebar");
      if (sidebar) sidebar.classList.toggle("open");
      shell.classList.toggle("menu-open");
    });
  }

  /* ── 密码可见切换：自动给每个密码框内嵌小眼睛 ── */
  const EYE_OPEN = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function attachEye(input) {
    if (input.dataset.eyeAttached === "1") return;
    // 跳过已经手工放了按钮的（老写法），避免出现两个切换控件
    const parent = input.parentNode;
    if (parent && parent.querySelector("[data-toggle-password]")) return;

    input.dataset.eyeAttached = "1";
    const wrap = document.createElement("span");
    wrap.className = "pwd-wrap";
    parent.insertBefore(wrap, input);
    wrap.appendChild(input);

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pwd-eye";
    btn.tabIndex = -1;
    btn.setAttribute("aria-label", "显示密码");
    btn.innerHTML = EYE_OPEN;
    wrap.appendChild(btn);

    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.innerHTML = show ? EYE_OFF : EYE_OPEN;
      btn.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
      input.focus();
    });
  }

  function scanPasswords(root) {
    (root || document).querySelectorAll('input[type="password"]').forEach(attachEye);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scanPasswords(document));
  } else {
    scanPasswords(document);
  }

  // 兼容老的 data-toggle-password 按钮写法（仍可用）
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-toggle-password]");
    if (!btn) return;
    ev.preventDefault();
    const input = document.querySelector(btn.getAttribute("data-toggle-password"));
    if (!input) return;
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.textContent = show ? "隐藏" : "显示";
  });

  /* ── 表单提交后按钮自动禁用（防止重复提交） ── */
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    if (form.hasAttribute("data-no-busy")) return;
    if (ev.defaultPrevented) return;
    const btn = form.querySelector('button[type="submit"]');
    if (btn && !btn.disabled) {
      const original = btn.innerHTML;
      btn.innerHTML = btn.getAttribute("data-busy-label") || "处理中…";
      btn.disabled = true;
      // 若页面没跳走就恢复
      setTimeout(() => { btn.innerHTML = original; btn.disabled = false; }, 8000);
    }
  });
})();
