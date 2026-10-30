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
      const isIcon = !!btn.querySelector("svg") && !btn.textContent.trim();
      btn.disabled = true;
      if (isIcon) {
        // 图标按钮不能塞文字：会把圆形撑成胶囊、把图标挤歪，
        // 而且收藏/复制这类操作完成后页面立刻跳转，闪一下文字纯属噪音
        btn.classList.add("is-busy");
      } else {
        btn.innerHTML = label || "处理中…";
      }
      return () => {
        btn.disabled = false;
        btn.classList.remove("is-busy");
        btn.innerHTML = original;
      };
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

  /* ═══════════ 自定义下拉菜单 ═══════════ */
  /* 原生 <select> 的弹层由系统绘制，样式无法统一；这里把它隐藏，
     用一个样式受控的按钮 + 弹层替代，但**保留原生 select 作为数据源**，
     所以表单提交、onchange 监听、JS 直接赋值都照常工作。 */
  const SEL = {
    open: null,     // 当前展开的 select
    panel: null,    // 复用的面板元素
  };

  const ARROW = '<svg class="sel-arrow" viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">'
    + '<path d="M0 0h10L5 6z" fill="currentColor"/></svg>';

  function selLabel(select) {
    const opt = select.options[select.selectedIndex];
    const text = opt ? (opt.textContent || "").trim() : "";
    return text || "请选择";
  }

  function closePanel() {
    if (SEL.panel) {
      SEL.panel.remove();
      SEL.panel = null;
    }
    if (SEL.open) {
      const wrap = SEL.open.closest(".sel-wrap");
      if (wrap) wrap.classList.remove("open");
      SEL.open = null;
    }
  }

  function buildPanel(select, trigger) {
    closePanel();
    const panel = document.createElement("div");
    panel.className = "sel-panel";
    panel.setAttribute("role", "listbox");

    Array.from(select.options).forEach(function (opt) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "sel-option" + (opt.selected ? " on" : "");
      item.textContent = (opt.textContent || "").trim();
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", opt.selected ? "true" : "false");
      if (opt.disabled) {
        item.disabled = true;
        item.classList.add("disabled");
      }
      item.addEventListener("click", function () {
        if (opt.disabled) return;
        select.value = opt.value;
        closePanel();
        // 走原生事件，让 onchange="this.form.submit()" 之类的逻辑照常触发
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      panel.appendChild(item);
    });

    // 用 fixed 定位挂到 body：卡片是 overflow:hidden，挂在里面会被裁掉
    document.body.appendChild(panel);
    const r = trigger.getBoundingClientRect();
    panel.style.minWidth = Math.max(r.width, 140) + "px";

    const panelH = panel.offsetHeight;
    const below = window.innerHeight - r.bottom;
    // 下方放不下就向上弹
    if (below < panelH + 12 && r.top > panelH + 12) {
      panel.style.top = (r.top - panelH - 6) + "px";
    } else {
      panel.style.top = (r.bottom + 6) + "px";
    }
    panel.style.left = r.left + "px";

    const wrap = select.closest(".sel-wrap");
    if (wrap) wrap.classList.add("open");
    SEL.open = select;
    SEL.panel = panel;
  }

  function enhanceSelect(select) {
    if (select.dataset.selDone === "1") return;
    if (select.hasAttribute("data-native-select")) return;  // 需要原生外观的（如多选）可跳过
    select.dataset.selDone = "1";

    // ── 隐藏前先量好尺寸 ──
    // 各处的 select 宽度来源不同：普通表单靠基础规则 width:100%，
    // 筛选栏靠 `.filter-bar .filter-item select{width:130px}`，
    // toDevice 靠内联 flex:0 0 190px。这些规则都是针对 select 元素的，
    // 元素被包进 wrapper 后就不再生效，所以必须在包裹前把尺寸搬到 wrapper 上。
    const rect = select.getBoundingClientRect();
    const measured = Math.round(rect.width);
    const parent = select.parentNode;
    const cs = window.getComputedStyle(parent);
    const parentInner = parent.clientWidth
      - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0);
    // 宽度基本等于父容器内容宽 → 它是「撑满型」，wrapper 用 100% 即可
    const isFullWidth = parentInner > 0 && measured >= parentInner - 2;

    const inline = select.getAttribute("style") || "";
    const movedInline = inline.split(";").filter(function (decl) {
      const prop = (decl.split(":")[0] || "").trim().toLowerCase();
      return prop && /^(flex|width|min-width|max-width)$/.test(prop);
    }).join(";");

    const wrap = document.createElement("span");
    wrap.className = "sel-wrap";
    // 注意：不要把 select 原来的 class 拷到 wrap 上。
    // 那些样式（如 .api-filter-cat 的 padding: 8px 32px 8px 14px）是为
    // 原生 select 设计的，套到外层会让 wrap 比 trigger 高出一截。
    // 需要的紧凑样式改在 CSS 里直接作用于 .sel-trigger。
    if (select.className) {
      wrap.dataset.origClass = select.className.trim();
    }
    if (movedInline) {
      wrap.style.cssText = movedInline;
    } else if (!isFullWidth && measured > 1) {
      wrap.style.width = measured + "px";
    }
    if (select.classList.contains("api-filter-cat")) {
      wrap.classList.add("sel-compact");
    }

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("sel-native");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "sel-trigger";
    trigger.innerHTML = '<span class="sel-text"></span>' + ARROW;
    wrap.appendChild(trigger);

    const label = trigger.querySelector(".sel-text");
    const sync = function () { label.textContent = selLabel(select); };
    sync();

    // 选项被 JS 动态改过（如「获取已连接设备」）也要跟着更新
    try {
      new MutationObserver(sync).observe(select, { childList: true, subtree: true });
    } catch (e) { /* 老浏览器忽略 */ }
    select.addEventListener("change", sync);

    trigger.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (SEL.open === select) { closePanel(); return; }
      buildPanel(select, trigger);
    });
  }

  function scanSelects(root) {
    (root || document).querySelectorAll("select").forEach(enhanceSelect);
  }

  document.addEventListener("click", function (ev) {
    if (SEL.panel && !ev.target.closest(".sel-panel") && !ev.target.closest(".sel-trigger")) {
      closePanel();
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closePanel();
  });
  window.addEventListener("resize", closePanel);
  // 页面滚动时面板位置会错，直接收起（比跟随定位简单可靠）
  window.addEventListener("scroll", closePanel, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => scanSelects(document));
  } else {
    scanSelects(document);
  }

  /* ═══════════ 接口列表：分类 + 搜索的无刷新过滤 ═══════════ */
  /* 页面一次性渲染了全部可见接口，这里只做客户端过滤：
     切换分类 / 输入关键词都不回服务器，因此不会刷新页面。
     同时把状态写进 URL（replaceState），刷新或分享链接仍能复现。
     没有 JS 时分类链接本身带 href，回车搜索也能提交，功能不丢。 */
  (function apiFilter() {
    const grid = document.querySelector("[data-api-grid]");
    const nav = document.querySelector(".api-cats");
    if (!grid || !nav) return;

    const cards = Array.from(grid.querySelectorAll(".api-card-wrap"));
    const searchForm = document.querySelector("[data-api-search]");
    const input = searchForm ? searchForm.querySelector('input[name="q"]') : null;
    const countEl = document.querySelector("[data-api-count]");
    const clearBtn = searchForm ? searchForm.querySelector("[data-api-clear]") : null;
    const emptyEl = document.querySelector("[data-api-empty]");

    let activeCat = "";
    let keyword = "";

    // 原始文案先存下来：标红会改 innerHTML，反复输入时得从干净文本重算
    cards.forEach(function (card) {
      card._name = card.getAttribute("data-name") || "";
      card._desc = card.getAttribute("data-desc") || "";
      card._cat = card.getAttribute("data-cat") || "";
      card._nameEl = card.querySelector(".js-name");
      card._descEl = card.querySelector(".js-desc");
    });

    function escapeHtml(s) {
      return String(s).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    }

    // 与后端 highlight 过滤器同语义：先整体转义再包 <mark>，避免 XSS
    function mark(text, kw) {
      if (!kw) return escapeHtml(text);
      const lower = text.toLowerCase();
      const needle = kw.toLowerCase();
      let out = "";
      let i = 0;
      while (true) {
        const at = lower.indexOf(needle, i);
        if (at < 0) { out += escapeHtml(text.slice(i)); break; }
        out += escapeHtml(text.slice(i, at)) +
          "<mark>" + escapeHtml(text.slice(at, at + needle.length)) + "</mark>";
        i = at + needle.length;
      }
      return out;
    }

    function apply() {
      const kw = keyword.trim();
      // 未分类在目录里是哨兵 __none__，而卡片 data-cat 是空串，比较前要还原
      const wantCat = activeCat === "__none__" ? "" : activeCat;
      let shown = 0;
      // 关键词命中数按分类累计，用来实时刷新目录里的数字
      const hit = {};

      cards.forEach(function (card) {
        const okCat = !activeCat || card._cat === wantCat;
        let okKw = true;
        if (kw) {
          const hay = (card._name + "\n" + card._desc).toLowerCase();
          okKw = hay.indexOf(kw.toLowerCase()) >= 0;
          if (okKw) hit[card._cat] = (hit[card._cat] || 0) + 1;
        }
        const visible = okCat && okKw;
        card.hidden = !visible;
        if (visible) shown += 1;

        // 只重建文本容器，不碰 NEW 标签与收藏按钮
        if (card._nameEl) card._nameEl.innerHTML = mark(card._name, kw);
        if (card._descEl) card._descEl.innerHTML = mark(card._desc, kw);
      });

      // 分类导航：高亮 + 数字跟随关键词
      // 搜索时数字变成「该分类下的命中数」，没有命中的分类直接置灰，
      // 这样一眼能看出关键词落在哪个分类里。
      nav.querySelectorAll(".api-cat").forEach(function (a) {
        const key = a.getAttribute("data-cat") || "";
        a.classList.toggle("on", key === activeCat);

        const numEl = a.querySelector(".api-cat-num");
        if (!numEl) return;
        if (!numEl.dataset.total) numEl.dataset.total = numEl.textContent.trim();
        if (!kw) {
          numEl.textContent = numEl.dataset.total;
          a.classList.remove("is-empty");
          return;
        }
        // 「全部接口」= 总命中数；其余按各自分类取
        const n = key === "" ? shown : (hit[key === "__none__" ? "" : key] || 0);
        numEl.textContent = n;
        a.classList.toggle("is-empty", n === 0);
      });

      if (countEl) {
        countEl.textContent = (kw || activeCat) ? ("显示 " + shown + " / " + cards.length) : "";
      }
      if (clearBtn) clearBtn.hidden = !(kw || activeCat);
      if (emptyEl) emptyEl.hidden = shown > 0;
    }

    function syncUrl() {
      const url = new URL(window.location.href);
      if (activeCat) url.searchParams.set("category", activeCat);
      else url.searchParams.delete("category");
      if (keyword.trim()) url.searchParams.set("q", keyword.trim());
      else url.searchParams.delete("q");
      window.history.replaceState(null, "", url.toString());
    }

    function setCat(cat, push) {
      activeCat = cat || "";
      apply();
      if (push !== false) syncUrl();
    }

    nav.addEventListener("click", function (ev) {
      const a = ev.target.closest(".api-cat");
      if (!a) return;
      // 只拦左键普通点击；Ctrl/中键等仍走链接（可新标签打开）
      if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      ev.preventDefault();
      input && (input.value = "");
      keyword = "";
      setCat(a.getAttribute("data-cat") || "");
      // 切分类后回到顶部，否则可能停在空白区
      grid.scrollIntoView({ block: "nearest" });
    });

    if (input) {
      let timer = null;
      input.addEventListener("input", function () {
        keyword = input.value;
        // 搜索时自动回到「全部接口」：否则在某个分类下输入，
        // 会被那个分类同时约束，搜到的东西看起来就「没跟上」。
        if (keyword.trim() && activeCat) {
          activeCat = "";
        }
        clearTimeout(timer);
        // 输入时防抖，避免每个字符都重排全部卡片
        timer = setTimeout(function () { apply(); syncUrl(); }, 120);
      });
    }

    if (searchForm) {
      // 阻止刷新：有 JS 时搜索就是本地过滤
      searchForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (input) keyword = input.value;
        if (keyword.trim() && activeCat) activeCat = "";
        apply();
        syncUrl();
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        if (input) input.value = "";
        keyword = "";
        setCat("");
      });
    }

    // 初始状态：URL 里的 q / category 优先，其次用服务端渲染时的选中态
    const params = new URLSearchParams(window.location.search);
    const initCat = params.get("category");
    const initKw = params.get("q");
    if (initKw !== null) {
      keyword = initKw;
      if (input) input.value = initKw;
    }
    // 有关键词时一律落在「全部接口」，避免 q 与 category 叠加后搜不到东西
    if (keyword.trim()) {
      activeCat = "";
    } else if (initCat !== null) {
      activeCat = initCat;
    } else {
      const on = nav.querySelector(".api-cat.on");
      activeCat = on ? (on.getAttribute("data-cat") || "") : "";
    }
    apply();
  })();

  /* ── 表单提交后按钮自动禁用（防止重复提交） ── */
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    if (form.hasAttribute("data-no-busy")) return;
    if (ev.defaultPrevented) return;
    // 按钮不一定在表单内部：顶栏的保存按钮是用 form="id" 关联到表单的，
    // 所以除了 form 内查找，还要用 document 找带 form 属性的按钮。
    let btn = form.querySelector('button[type="submit"]');
    if (!btn && form.id) {
      btn = document.querySelector('button[type="submit"][form="' + form.id + '"]');
    }
    if (btn && !btn.disabled) {
      const original = btn.innerHTML;
      // 图标按钮不写文字：会把圆形撑成胶囊、图标被挤歪
      const isIcon = !!btn.querySelector("svg") && !btn.textContent.trim();
      btn.disabled = true;
      if (isIcon) {
        btn.classList.add("is-busy");
      } else {
        btn.innerHTML = btn.getAttribute("data-busy-label") || "处理中…";
      }
      // 若页面没跳走就恢复
      setTimeout(() => {
        btn.innerHTML = original;
        btn.disabled = false;
        btn.classList.remove("is-busy");
      }, 8000);
    }
  });
})();
