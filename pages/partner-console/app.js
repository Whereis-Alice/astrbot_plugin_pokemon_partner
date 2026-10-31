/**
 * 管理台视图层。
 *
 * 结构：每个视图一个 initializer，返回 `{ load, refresh }`；
 * 切换视图时懒加载，刷新按钮重跑当前视图的 load。
 */

import {
  api,
  apiPost,
  bridge,
  applyTheme,
  confirmAction,
  bytes,
  clear,
  currentTheme,
  el,
  num,
  paintIcons,
  RARITY_COLOR,
  RARITY_LABEL,
  ready,
  text,
  toast,
  typeColor,
  typeStyle,
} from "./js/core.js";
import { learnsetExplorer } from "./js/learnset.js";

const state = {
  view: "overview",
  loaded: new Set(),
  views: {},
  dexTypes: new Set(),
  dexGens: new Set(),
};

/* ================================================================= 路由 */

async function showView(name) {
  if (!state.views[name]) return;
  state.view = name;
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  for (const button of document.querySelectorAll(".nav-item")) {
    button.classList.toggle("active", button.dataset.view === name);
    if (button.dataset.view === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (location.hash.slice(1) !== name) {
    history.replaceState(null, "", `#${name}`);
  }
  const labels = { overview: "总览", dex: "宝可梦图鉴", members: "训练家管理", rules: "概率与规则", settings: "研究站设置" };
  document.getElementById("viewBreadcrumb").textContent = labels[name];
  const notice = document.getElementById("pageNotice");
  notice.hidden = true;
  const view = state.views[name];
  if (!state.loaded.has(name)) {
    try {
      await view.load();
      state.loaded.add(name);
    } catch (error) {
      if (state.view === name) {
        notice.hidden = false;
        clear(notice);
        notice.append(el("span", { text: error.message || "页面加载失败" }), el("button", {
          type: "button", class: "button ghost small", text: "重新加载", onclick: () => showView(name),
        }));
      }
    }
  }
}

async function refreshView() {
  const view = state.views[state.view];
  if (!view) return;
  const button = document.getElementById("refreshButton");
  if (button.disabled) return;
  button.disabled = true;
  button.classList.add("loading");
  try {
    const refreshed = await (view.refresh ?? view.load)();
    if (refreshed === false) return;
    state.loaded.add(state.view);
    document.getElementById("pageNotice").hidden = true;
    toast("已更新当前页面", "success");
  } catch (error) {
    toast(error.message || "刷新失败", "error");
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

/* ================================================================ 概览 */

function setupOverview() {
  const statsBox = document.getElementById("overviewStats");
  const poolBox = document.getElementById("poolBars");
  const healthBox = document.getElementById("healthList");

  const load = async () => {
    const data = await api("/overview");
    const s = data.stats ?? {};
    const items = [
      ["训练家", num(s.trainers), "至少收集过 1 位宝可梦"],
      ["图鉴收录", num(s.collected), "全部会话累计变体数"],
      ["闪光变体", num(s.shiny), "含闪光首次获得"],
      ["累计抽取", num(s.draws), "所有会话总次数"],
      ["今日活跃", num(s.active_today), "今天有抽取记录"],
      ["会话数", num(s.sessions), "群聊与私聊"],
    ];
    clear(statsBox);
    for (const [label, value, note] of items) {
      statsBox.append(
        el("div", { class: "stat" }, [
          el("div", { class: "stat-label", text: label }),
          el("div", { class: "stat-value", text: value }),
          el("div", { class: "stat-note", text: note }),
        ]),
      );
    }

    const pool = data.pool ?? {};
    const total = Object.values(pool).reduce((sum, v) => sum + Number(v || 0), 0) || 1;
    clear(poolBox);
    for (const rarity of ["SSR", "SR", "R", "C"]) {
      const count = Number(pool[rarity] || 0);
      poolBox.append(rarityRow(rarity, (count / total) * 100, `${count} 位`));
    }

    const health = data.health ?? {};
    clear(healthBox);
    const rows = [
      ["数据文件", health.state_path || "—"],
      ["文件大小", bytes(health.state_bytes)],
      ["从备份恢复", health.restored_from_backup ? "是（可能丢失最后一次写入）" : "否"],
      ["写入已锁定", health.load_failed ? "是（数据损坏，需人工检查）" : "否"],
      ["每次抽取", `${data.rules?.draws_per_command ?? "—"} 位`],
      ["每日次数", `${data.rules?.daily_limit ?? "—"} 次`],
      ["SSR 保底", `${data.rules?.ssr_pity_after ?? "—"} 次`],
      ["闪光概率", `${data.rules?.shiny_chance_percent ?? "—"}%`],
    ];
    for (const [label, value] of rows) {
      healthBox.append(el("dt", { text: label }));
      healthBox.append(el("dd", { text: value }));
    }
    const station = document.querySelector(".station-status");
    station.lastChild.textContent = health.load_failed ? "数据异常，已保护写入" : "本地图鉴已就绪";
    station.classList.toggle("unhealthy", Boolean(health.load_failed));
    if (health.load_failed) {
      toast("数据文件损坏且备份不可用，插件已停止写入，请检查服务器文件。", "error", 8000);
    }
    if (!document.querySelector("#heroArt > img")) {
      api("/dex/detail", { key: "25" }).then((featured) => {
        if (!featured.artwork || document.querySelector("#heroArt > img")) return;
        const art = el("img", { src: featured.artwork, alt: "皮卡丘官方立绘", decoding: "async" });
        art.addEventListener("error", () => { art.remove(); document.querySelector(".hero-placeholder").hidden = false; });
        document.querySelector(".hero-placeholder").hidden = true;
        document.getElementById("heroArt").append(art);
      }).catch(() => { /* 焦点立绘缺失时保留精灵球，不影响管理功能。 */ });
    }
  };

  return { load };
}

function rarityRow(rarity, percent, meta) {
  const width = Math.max(0, Math.min(100, Number(percent) || 0));
  return el("div", { class: "rarity-row" }, [
    el("span", { class: "rarity-name" }, [
      el("span", {
        class: "rarity-dot",
        style: `background:${RARITY_COLOR[rarity]}`,
      }),
      `${rarity} · ${RARITY_LABEL[rarity]}`,
    ]),
    el("div", { class: "rarity-track" }, [
      el("div", {
        class: "rarity-fill",
        style: `width:${width}%;background:${RARITY_COLOR[rarity]}`,
      }),
    ]),
    el("span", { class: "rarity-meta", text: `${percent.toFixed(1)}% · ${meta}` }),
  ]);
}

/* ================================================================ 图鉴 */

function setupDex() {
  const search = document.getElementById("dexSearch");
  const typeSel = document.getElementById("dexType");
  const genSel = document.getElementById("dexGen");
  const raritySel = document.getElementById("dexRarity");
  const formsBox = document.getElementById("dexForms");
  const grid = document.getElementById("dexGrid");
  const meta = document.getElementById("dexMeta");
  const pager = document.getElementById("dexPager");
  const detail = document.getElementById("dexDetail");
  const backdrop = document.getElementById("dexDrawerBackdrop");

  const page = { index: 1, size: 24, total: 0 };
  let selected = null;
  let lastTrigger = null;
  let optionsReady = false;

  const collectOptions = (data) => {
    if (optionsReady) return;
    optionsReady = true;
    const types = data.types ?? ["一般", "火", "水", "草", "电", "冰", "格斗", "毒", "地面", "飞行", "超能力", "虫", "岩石", "幽灵", "龙", "恶", "钢", "妖精"];
    const generations = data.generations ?? [1, 2, 3, 4, 5, 6, 7, 8, 9];
    for (const type of types) typeSel.append(el("option", { value: type, text: type }));
    for (const gen of generations) genSel.append(el("option", { value: gen, text: `第 ${gen} 世代` }));
  };
  let loadVersion = 0;
  let detailVersion = 0;
  let layout = "gallery";
  try { layout = localStorage.getItem("partner-console-dex-layout") || "gallery"; } catch { /* 存储可选 */ }
  const setLayout = (value) => {
    layout = value === "compact" ? "compact" : "gallery";
    grid.classList.toggle("compact", layout === "compact");
    for (const button of document.querySelectorAll("[data-dex-layout]")) button.setAttribute("aria-pressed", String(button.dataset.dexLayout === layout));
    try { localStorage.setItem("partner-console-dex-layout", layout); } catch { /* 存储可选 */ }
  };
  setLayout(layout);
  for (const button of document.querySelectorAll("[data-dex-layout]")) button.addEventListener("click", () => setLayout(button.dataset.dexLayout));

  const renderPager = () => {
    clear(pager);
    const pages = Math.max(1, Math.ceil(page.total / page.size));
    if (pages <= 1) return;
    const go = (index) => {
      page.index = index;
      load().catch((error) => toast(error.message, "error"));
    };
    pager.append(
      el("button", {
        type: "button",
        text: "上一页",
        disabled: page.index <= 1,
        onclick: () => go(page.index - 1),
      }),
    );
    const windowStart = Math.max(1, Math.min(page.index - 2, pages - 4));
    const windowEnd = Math.min(pages, windowStart + 4);
    for (let i = windowStart; i <= windowEnd; i += 1) {
      pager.append(
        el("button", {
          type: "button",
          text: String(i),
          "aria-current": String(i === page.index),
          onclick: () => go(i),
        }),
      );
    }
    pager.append(
      el("button", {
        type: "button",
        text: "下一页",
        disabled: page.index >= pages,
        onclick: () => go(page.index + 1),
      }),
    );
  };

  const load = async () => {
    const version = ++loadVersion;
    grid.setAttribute("aria-busy", "true");
    meta.textContent = "正在查阅图鉴…";
    let data;
    try { data = await api("/dex", {
      q: search.value.trim(),
      type: typeSel.value,
      gen: genSel.value,
      rarity: raritySel.value,
      forms: formsBox.checked ? 1 : 0,
      page: page.index,
      size: page.size,
    }); } catch (error) {
      if (version !== loadVersion) return;
      grid.setAttribute("aria-busy", "false");
      meta.textContent = "图鉴加载失败，请重试";
      throw error;
    }
    if (version !== loadVersion) return;
    grid.setAttribute("aria-busy", "false");
    page.total = data.total ?? 0;
    collectOptions(data);
    clear(grid);
    if (!data.items?.length) {
      grid.append(el("div", { class: "empty-state", text: "没有符合条件的宝可梦。" }));
    }
    for (const item of data.items ?? []) {
      const itemKey = JSON.stringify([item.id_raw, item.form_name || ""]);
      const card = el(
        "button",
        {
          type: "button",
          class: `dex-card${selected === itemKey ? " selected" : ""}`,
          dataset: { rarity: item.rarity || "C" },
          style: `--dex-type:${typeColor(item.types?.[0])}`,
          "aria-label": `${item.name_zh}${item.form_name ? `（${item.form_name}）` : ""}，打开图鉴详情`,
          onclick: () => {
            lastTrigger = card;
            selected = itemKey;
            for (const node of grid.children) node.classList?.remove("selected");
            card.classList.add("selected");
            loadDetail(item).catch((error) => {
              if (selected !== itemKey || detail.hidden) return;
              const content = mountDrawer();
              content.append(
                el("div", { class: "empty-state", text: error.message }),
              );
              toast(error.message, "error", 6000);
            });
          },
        },
        [
          el("div", { class: "dex-card-top" }, [
            el("span", { class: "dex-id", text: `NO. ${String(item.id_raw).padStart(3, "0")}` }),
            item.rarity ? el("span", { class: `chip rarity-${item.rarity}`, text: item.rarity }) : null,
          ]),
          artworkFigure(item.artwork, item.name_zh),
          el("span", { class: "dex-name", text: item.name_zh + (item.form_name ? `（${item.form_name}）` : "") }),
          el("span", { class: "dex-sub", text: item.name_en || item.category }),
          el("div", { class: "dex-bottom" }, [
            el("div", { class: "chips" }, (item.types ?? []).map((type) => el("span", { class: "chip", style: typeStyle(type), text: type }))),
            el("span", { class: "dex-bst" }, ["BST ", el("b", { text: item.bst || "—" })]),
          ]),
        ],
      );
      grid.append(card);
    }
    meta.textContent = `共 ${num(page.total)} 条 · 第 ${page.index}/${Math.max(1, Math.ceil(page.total / page.size))} 页`;
    renderPager();
  };

  const closeDetail = () => {
    ++detailVersion;
    detail.hidden = true;
    backdrop.hidden = true;
    document.body.classList.remove("drawer-open");
    document.dispatchEvent(new Event("partner-drawer-change"));
    document.getElementById("refreshButton").disabled = false;
    selected = null;
    for (const node of grid.children) node.classList?.remove("selected");
    lastTrigger?.focus?.({ preventScroll: true });
    lastTrigger = null;
  };

  const mountDrawer = () => {
    detail.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("drawer-open");
    document.dispatchEvent(new Event("partner-drawer-change"));
    clear(detail);
    const close = el(
      "button",
      {
        class: "icon-button drawer-close",
        type: "button",
        title: "关闭详情",
        "aria-label": "关闭详情",
        onclick: closeDetail,
      },
      [el("i", { "data-icon": "x" })],
    );
    const content = el("div", { class: "drawer-content" });
    detail.append(el("div", { class: "drawer-toolbar" }, [
      el("span", { text: "POKÉDEX / 图鉴档案", "aria-hidden": "true" }), close,
    ]), content);
    paintIcons(detail);
    document.getElementById("nav").inert = true;
    document.getElementById("refreshButton").disabled = true;
    detail.focus({ preventScroll: true });
    return content;
  };

  const loadDetail = async (item) => {
    const version = ++detailVersion;
    const content = mountDrawer();
    content.append(el("p", { class: "placeholder", text: "加载中…" }));
    const data = await api("/dex/detail", { key: item.id_raw, form: item.form_name });
    if (version !== detailVersion || detail.hidden) return;
    const row = data.row ?? {};
    clear(content);
    const displayName = row.form_name || row.name_zh || item.name_zh;
    detail.setAttribute("aria-label", `${displayName} 图鉴详情`);

    content.append(
      el("div", { class: "detail-head" }, [
        el("div", {}, [
          el("div", { class: "detail-title", text: displayName }),
          el("div", {
            class: "detail-en",
            text: [row.name_en, row.name_ja].filter(Boolean).join(" · "),
          }),
        ]),
        data.rarity
          ? el("span", { class: `chip rarity-${data.rarity}`, text: `${data.rarity} · ${RARITY_LABEL[data.rarity]}` })
          : null,
      ]),
    );

    const art = artworkFigure(data.artwork, row.name_zh || item.name_zh, true);
    art.style.setProperty("--dex-type", typeColor((row.types || "").split("/")[0]));
    content.append(art);

    content.append(
      el("div", { class: "chips" }, [
        ...(row.types || "")
          .split("/")
          .filter(Boolean)
          .map((type) =>
            el("span", { class: "chip", style: typeStyle(type), text: type }),
          ),
        el("span", { class: "chip ghost", text: `#${row.id_raw}` }),
        el("span", { class: "chip ghost", text: `第 ${row.generation} 世代` }),
        el("span", { class: "chip ghost", text: row.category }),
      ]),
    );

    const stats = data.stats ?? {};
    const maxStat = 255; // 官方单项种族值统一尺度，避免跨宝可梦视觉误导。
    const bars = el("div", { class: "stat-bars" });
    for (const [label, value] of Object.entries(stats)) {
      bars.append(
        el("div", { class: "stat-bar" }, [
          el("span", { text: label }),
          el("div", { class: "track" }, [
            el("div", {
              class: "fill",
              style: `width:${Math.max(0, Math.min(100, (Number(value) / maxStat) * 100))}%;background:${({ HP: "#638b56", 攻击: "#d6764c", 防御: "#beaa54", 特攻: "#4d8fad", 特防: "#9975a8", 速度: "#d06575" })[label] || "var(--pk-accent)"}`,
            }),
          ]),
          el("span", { class: "value", text: String(value) }),
        ]),
      );
    }
    content.append(
      section("种族值", [
        bars,
        el("div", { class: "text-block", text: `总和 ${data.bst}｜特性：${row.abilities || "—"}${row.hidden_abilities ? `｜隐藏：${row.hidden_abilities}` : ""}` }),
      ]),
    );

    const matchups = [
      ["弱点", row.weak_to],
      ["抗性", row.resist_to],
      ["免疫", row.immune_to],
    ].filter(([, value]) => value);
    if (matchups.length) {
      content.append(
        section(
          "属性相性",
          matchups.map(([label, value]) =>
            el("div", { class: "text-block", text: `${label}：${value}` }),
          ),
        ),
      );
    }

    content.append(section("个体档案", [el("div", { class: "detail-data-grid" }, [
      ["身高", row.height], ["体重", row.weight], ["捕获度", row.catch_rate], ["蛋群", row.egg_groups], ["性别比例", row.gender_ratio], ["世代", row.generation ? `第 ${row.generation} 世代` : ""],
    ].map(([label, value]) => el("div", { class: "detail-data" }, [el("span", { text: label }), value ?? "—"]))) ]));
    if (row.evolution) content.append(
      section("进化路线", [
        el("div", {
          class: "text-block",
          text: row.evolution,
        }),
      ]),
    );

    content.append(section("可学招式", [learnsetExplorer(data)]));

    if (data.profile) {
      content.append(section("图鉴描述", [clampedText(data.profile)]));
    }
    if (data.dex_entries) {
      content.append(section("历代图鉴", [clampedText(data.dex_entries)]));
    }
  };

  let timer = 0;
  const debounce = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      page.index = 1;
      load().catch((error) => toast(error.message, "error"));
    }, 220);
  };

  search.addEventListener("input", debounce);
  for (const node of [typeSel, genSel, raritySel]) {
    node.addEventListener("change", () => {
      page.index = 1;
      load().catch((error) => toast(error.message, "error"));
    });
  }
  formsBox.addEventListener("change", () => {
    page.index = 1;
    load().catch((error) => toast(error.message, "error"));
  });
  document.getElementById("dexClear").addEventListener("click", () => {
    search.value = typeSel.value = genSel.value = raritySel.value = "";
    formsBox.checked = false;
    page.index = 1;
    load().catch((error) => toast(error.message, "error"));
  });
  backdrop.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (event) => {
    if (detail.hidden) return;
    if (event.key === "Escape") closeDetail();
    if (event.key === "Tab") {
      const focusable = [...detail.querySelectorAll("button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex='0']")].filter((node) => node.getClientRects().length);
      const first = focusable[0]; const last = focusable.at(-1);
      if (!first) { event.preventDefault(); detail.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === detail)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === detail)) { event.preventDefault(); first.focus(); }
    }
  });

  return { load };
}

function artworkFigure(src, name, large = false) {
  const figure = el(large ? "figure" : "div", { class: large ? "detail-art" : "dex-art" });
  const fallback = () => {
    clear(figure);
    figure.append(el("span", { class: "pokeball", "aria-hidden": "true" }), el("span", {
      class: large ? "detail-art-note" : "art-note", text: "暂无本地立绘",
    }));
  };
  if (!src) fallback();
  else {
    const img = el("img", { src, alt: `${name} 官方立绘`, loading: large ? "eager" : "lazy", decoding: "async" });
    img.addEventListener("error", fallback, { once: true });
    figure.append(img);
  }
  return figure;
}

function section(label, children) {
  return el("div", {}, [el("div", { class: "section-label", text: label }), ...children]);
}

function clampedText(value) {
  const box = el("div", { class: "text-block clamped", text: value });
  const button = el("button", {
    type: "button",
    class: "expand-button",
    text: "展开阅读",
    "aria-expanded": "false",
    onclick: () => {
      const open = box.classList.toggle("clamped");
      button.textContent = open ? "展开阅读" : "收起";
      button.setAttribute("aria-expanded", String(!open));
    },
  });
  return el("div", {}, [box, button]);
}

/* ============================================================ 宝可梦管理 */

function setupMembers() {
  const scopeSel = document.getElementById("memberScope");
  const search = document.getElementById("memberSearch");
  const tableBody = document.querySelector("#memberTable tbody");
  const meta = document.getElementById("memberMeta");
  const pager = document.getElementById("memberPager");
  const resetScope = document.getElementById("resetScopeButton");
  const exportScope = document.getElementById("exportScopeButton");
  let memberVersion = 0;
  let updating = false;
  exportScope.addEventListener("click", async () => {
    if (exportScope.disabled) return;
    exportScope.disabled = true;
    try {
      const scope = scopeSel.value;
      const stamp = new Date().toISOString().slice(0, 10);
      await bridge.download("sessions/export", { scope }, `pokemon-partner-${scope ? "session" : "all"}-${stamp}.json`);
      toast("收藏数据已导出", "success");
    } catch (error) { toast(`导出失败：${error.message}`, "error"); }
    finally { exportScope.disabled = false; }
  });
  const page = { index: 1, size: 20, total: 0 };
  let sessionsLoaded = false;

  const loadSessions = async (force = false) => {
    if (sessionsLoaded && !force) return;
    const data = await api("/sessions");
    const current = scopeSel.value;
    clear(scopeSel);
    scopeSel.append(el("option", { value: "", text: "全部会话" }));
    for (const item of data.items ?? []) {
      const label =
        item.kind === "group" ? `${item.scope.replace("group:", "群 ")}` : item.scope.replace("private:", "私聊 ");
      scopeSel.append(
        el("option", {
          value: item.scope,
          text: `${label} · ${item.members} 人 · ${item.collected} 位`,
        }),
      );
    }
    scopeSel.value = [...scopeSel.options].some((option) => option.value === current) ? current : "";
    sessionsLoaded = true;
  };

  const renderPager = () => {
    clear(pager);
    const pages = Math.max(1, Math.ceil(page.total / page.size));
    if (pages <= 1) return;
    const go = (index) => {
      page.index = index;
      load().catch((error) => toast(error.message, "error"));
    };
    pager.append(
      el("button", {
        type: "button",
        text: "上一页",
        disabled: page.index <= 1,
        onclick: () => go(page.index - 1),
      }),
      el("span", { class: "rarity-meta", text: `${page.index} / ${pages}` }),
      el("button", {
        type: "button",
        text: "下一页",
        disabled: page.index >= pages,
        onclick: () => go(page.index + 1),
      }),
    );
  };

  const patch = async (body, okMessage) => {
    if (updating) return;
    if (body.field === "dust" && (!Number.isSafeInteger(body.value) || body.value < 0)) {
      toast("星尘需要填写非负整数", "error"); return;
    }
    updating = true;
    try {
      await apiPost("/members/update", body);
      state.loaded.delete("overview");
      toast(okMessage, "success");
      await load();
      await loadSessions(true);
    } catch (error) {
      toast(error.message, "error");
    } finally { updating = false; }
  };

  const removeMember = async (scope, userId, name) => {
    if (updating) return;
    updating = true;
    const approved = await confirmAction({ title: `删除 ${name} 的收藏数据？`, message: `仅删除 ${scope} 中这位成员的收藏、星尘和抽取进度，其他成员保留。此操作不可撤销，建议先导出备份。`, confirmLabel: "删除该成员", danger: true });
    if (!approved) { updating = false; return; }
    const buttons = [...tableBody.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    scopeSel.disabled = true;
    try {
      const result = await apiPost("/sessions/delete", { scope, user_id: userId });
      toast(result.removed ? "已删除该成员数据，其他成员已保留" : "该成员已不存在，列表已刷新", "success");
      await loadSessions(true);
      await load();
      state.loaded.delete("overview");
    } catch (error) {
      toast(`删除失败：${error.message}`, "error");
    } finally { updating = false; scopeSel.disabled = false; buttons.forEach((button) => { button.disabled = false; }); }
  };

  const load = async () => {
    const version = ++memberVersion;
    meta.textContent = "正在读取训练家数据…";
    let data;
    try { data = await api("/members", {
      scope: scopeSel.value,
      q: search.value.trim(),
      page: page.index,
      size: page.size,
    }); } catch (error) {
      if (version !== memberVersion) return false;
      meta.textContent = "训练家数据加载失败，请重试";
      throw error;
    }
    if (version !== memberVersion) return false;
    page.total = data.total ?? 0;
    const pages = Math.max(1, Math.ceil(page.total / page.size));
    if (page.index > pages) { page.index = pages; return load(); }
    clear(tableBody);
    if (!data.items?.length) {
      tableBody.append(
        el("tr", {}, [
          el("td", { colspan: "8" }, [
            el("div", { class: "empty-state", text: "没有匹配的成员。" }),
          ]),
        ]),
      );
    }
    for (const item of data.items ?? []) {
      const dustInput = el("input", {
        class: "inline-input",
        type: "number",
        min: "0",
        value: String(item.dust),
        "aria-label": `${item.nickname} 的星尘`,
      });
      tableBody.append(
        el("tr", {}, [
          el("td", {}, [
            el("div", { class: "member-name" }, [
              el("strong", { text: item.nickname }),
              el("small", { text: item.user_id }),
            ]),
          ]),
          el("td", {}, [el("span", { class: "scope-tag", text: item.scope })]),
          el("td", { class: "num", text: String(item.collected) }),
          el("td", { class: "num", text: String(item.shiny) }),
          el("td", { class: "num" }, [dustInput]),
          el(
            "td",
            {
              class: "num",
              title: `基础次数 + 额外 ${item.bonus_today}`,
              text: `${item.used_today}${item.bonus_today ? ` (+${item.bonus_today})` : ""}`,
            },
          ),
          el("td", { class: "num", text: String(item.pity_ssr) }),
          el("td", {}, [
            el("div", { class: "cell-actions" }, [
              el("button", {
                type: "button",
                class: "button ghost small",
                text: "存星尘",
                onclick: () =>
                  patch(
                    {
                      scope: item.scope,
                      user_id: item.user_id,
                      field: "dust",
                      value: Number(dustInput.value || 0),
                    },
                    "星尘已更新",
                  ),
              }),
              el("button", {
                type: "button",
                class: "button ghost small",
                text: "+1 次",
                onclick: () =>
                  patch(
                    {
                      scope: item.scope,
                      user_id: item.user_id,
                      action: "add_bonus",
                      value: 1,
                    },
                    "已增加 1 次",
                  ),
              }),
              el("button", {
                type: "button",
                class: "button ghost small",
                text: "重置今日",
                onclick: () =>
                  patch(
                    { scope: item.scope, user_id: item.user_id, action: "reset" },
                    "已重置今日次数",
                  ),
              }),
              el("button", {
                type: "button",
                class: "button ghost small",
                text: "删除",
                "aria-label": `删除 ${item.nickname} 的收藏数据`,
                onclick: () => removeMember(item.scope, item.user_id, item.nickname),
              }),
            ]),
          ]),
        ]),
      );
    }
    meta.textContent = `共 ${num(page.total)} 位成员`;
    renderPager();
  };

  scopeSel.addEventListener("change", () => {
    page.index = 1;
    load().catch((error) => toast(error.message, "error"));
  });
  let timer = 0;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      page.index = 1;
      load().catch((error) => toast(error.message, "error"));
    }, 220);
  });
  resetScope.addEventListener("click", async () => {
    const scope = scopeSel.value;
    if (!scope) {
      toast("请先选择一个会话", "error");
      return;
    }
    if (!(await confirmAction({ title: "重置今日次数？", message: `重置 ${scope} 全部成员今天的抽取次数，收藏和星尘保留。`, confirmLabel: "重置今日次数" }))) return;
    try {
      const result = await apiPost("/sessions/reset", { scope });
      state.loaded.delete("overview");
      toast(`已重置 ${result.changed ?? 0} 位成员`, "success");
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  return {
    load: async () => {
      await loadSessions();
      return load();
    },
    refresh: async () => {
      await loadSessions(true);
      return load();
    },
  };
}

/* ================================================================ 规则 */

function setupRules() {
  const bars = document.getElementById("ruleBars");
  const limits = document.getElementById("ruleLimits");
  const economy = document.querySelector("#ruleEconomy tbody");

  const load = async () => {
    const data = await api("/rules");
    clear(bars);
    for (const rarity of ["SSR", "SR", "R", "C"]) {
      const configured = data.configured?.[rarity] ?? {};
      const effective = data.effective?.[rarity];
      const effectiveText = effective
        ? `实际 ${effective.percent}%`
        : "实际 0%（卡池为空）";
      bars.append(
        rarityRow(
          rarity,
          Number(configured.percent ?? 0),
          `配置 ${configured.percent ?? 0}% · ${effectiveText} · 池 ${configured.pool ?? 0}`,
        ),
      );
    }

    clear(limits);
    const rows = [
      ["R 阈值（种族值）", data.thresholds?.rare_min_bst],
      ["SR 阈值", data.thresholds?.super_rare_min_bst],
      ["SSR 阈值", data.thresholds?.ultra_rare_min_bst],
      ["每次抽取", `${data.limits?.draws_per_command} 位`],
      ["每日次数", `${data.limits?.daily_limit} 次`],
      ["冷却", `${data.limits?.cooldown_seconds} 秒`],
      ["SSR 保底", data.limits?.ssr_pity_after ? `${data.limits.ssr_pity_after} 次` : "关闭"],
      ["新宝可梦保底", data.limits?.new_pity_after ? `${data.limits.new_pity_after} 次` : "关闭"],
      ["允许重复", data.limits?.allow_duplicate_draw ? "是" : "否"],
      ["闪光概率", `${data.economy?.shiny_chance_percent}%`],
      ["闪光重复倍率", `×${data.economy?.shiny_dust_multiplier}`],
      ["闪光兑换倍率", `×${data.economy?.shiny_spark_multiplier}`],
    ];
    for (const [label, value] of rows) {
      limits.append(el("dt", { text: label }));
      limits.append(el("dd", { text: value === undefined ? "—" : String(value) }));
    }

    clear(economy);
    for (const rarity of ["C", "R", "SR", "SSR"]) {
      economy.append(
        el("tr", {}, [
          el("td", {}, [
            el("span", { class: `chip rarity-${rarity}`, text: `${rarity} · ${RARITY_LABEL[rarity]}` }),
          ]),
          el("td", { class: "num", text: String(data.economy?.duplicate_dust?.[rarity] ?? 0) }),
          el("td", { class: "num", text: String(data.economy?.spark_costs?.[rarity] ?? 0) }),
        ]),
      );
    }
  };

  return { load };
}

/* ================================================================ 设置 */

const SETTINGS_FIELDS = [
  {
    group: "今日宝可梦卡面",
    fields: [
      { key: "daily_card_style", label: "今日宝可梦卡面风格", type: "select", default: "classic", options: [["classic", "现有稀有度卡面"], ["tcg", "TCG 收藏卡面"], ["yugioh", "游戏王收藏卡面"]], hint: "三套均发送文字提示与纯卡牌，卡内以属性图标显示弱点、抗性和免疫。仅切换今日宝可梦卡面；关闭图片卡片时使用文本。" },
      { key: "yugioh_profile_black_bold", label: "游戏王简介纯黑粗体", type: "bool", default: false, hint: "仅影响游戏王卡面「今日宝可梦」单抽和多抽的简介正文。开启后使用纯黑粗体，默认沿用当前字体；其他文字和卡面不变。" },
    ],
  },
  {
    group: "对战战报",
    fields: [
      { key: "battle_output_mode", label: "对战发送方式", type: "select", default: "card", options: [["card", "精美卡片（封面 + 两张长图）"], ["card_forward", "图片合并转发（QQ / OneBot）"], ["forward", "文字合并转发（QQ / OneBot）"], ["text", "文本战报"]], hint: "命令末尾可加「卡片 / 图片转发 / 转发 / 文本」覆盖本次选择。图片转发失败或平台不支持时改发普通卡片；图片生成失败时发送文本。" },
    ],
  },
  {
    group: "抽取规则",
    fields: [
      { key: "gacha_enabled", label: "启用宝可梦系统", type: "bool", hint: "关闭后聊天指令会提示未启用" },
      { key: "draws_per_command", label: "每次抽取数量", type: "number", min: 1, max: 10 },
      { key: "daily_draw_limit", label: "每日基础次数", type: "number", min: 0, max: 100 },
      { key: "draw_cooldown_seconds", label: "冷却（秒）", type: "number", min: 0, max: 3600, step: 0.5 },
      { key: "ssr_pity_after", label: "SSR 保底次数", type: "number", min: 0, max: 999, hint: "0 表示关闭" },
      { key: "new_pity_after", label: "新宝可梦保底次数", type: "number", min: 0, max: 999, hint: "0 表示关闭" },
      { key: "allow_duplicate_draw", label: "允许抽到已拥有", type: "bool" },
      { key: "include_alternate_forms", label: "形态计入卡池", type: "bool", hint: "开启后卡池 1229 项" },
    ],
  },
  {
    group: "稀有度",
    fields: [
      { key: "rarity_weights.C", label: "C 权重", type: "number", min: 0 },
      { key: "rarity_weights.R", label: "R 权重", type: "number", min: 0 },
      { key: "rarity_weights.SR", label: "SR 权重", type: "number", min: 0 },
      { key: "rarity_weights.SSR", label: "SSR 权重", type: "number", min: 0 },
      { key: "rare_min_bst", label: "R 最低种族值", type: "number", min: 0 },
      { key: "super_rare_min_bst", label: "SR 最低种族值", type: "number", min: 0 },
      { key: "ultra_rare_min_bst", label: "SSR 最低种族值", type: "number", min: 0 },
      { key: "shiny_chance_percent", label: "闪光概率 %", type: "number", min: 0, max: 100, step: 0.1 },
    ],
  },
  {
    group: "星尘经济",
    fields: [
      { key: "duplicate_dust.C", label: "C 重复星尘", type: "number", min: 0 },
      { key: "duplicate_dust.R", label: "R 重复星尘", type: "number", min: 0 },
      { key: "duplicate_dust.SR", label: "SR 重复星尘", type: "number", min: 0 },
      { key: "duplicate_dust.SSR", label: "SSR 重复星尘", type: "number", min: 0 },
      { key: "spark_costs.C", label: "C 兑换价格", type: "number", min: 0 },
      { key: "spark_costs.R", label: "R 兑换价格", type: "number", min: 0 },
      { key: "spark_costs.SR", label: "SR 兑换价格", type: "number", min: 0 },
      { key: "spark_costs.SSR", label: "SSR 兑换价格", type: "number", min: 0 },
      { key: "shiny_dust_multiplier", label: "闪光重复倍率", type: "number", min: 1, step: 0.5 },
      { key: "shiny_spark_multiplier", label: "闪光兑换倍率", type: "number", min: 1, step: 0.5 },
    ],
  },
  {
    group: "资源与网络",
    fields: [
      { key: "images_dir", label: "本地立绘目录", type: "text", wide: true, hint: "留空则使用网络官方图或占位图" },
      { key: "enable_image_card", label: "启用图片卡片", type: "bool" },
      { key: "fallback_web_image", label: "缺图时下载官方图", type: "bool" },
      { key: "pokeapi_enabled", label: "启用 PokéAPI 补充", type: "bool" },
      { key: "pokeapi_timeout", label: "PokéAPI 超时（秒）", type: "number", min: 1, max: 120 },
    ],
  },
];

function getPath(source, path) {
  return path.split(".").reduce((acc, key) => (acc == null ? acc : acc[key]), source);
}

function setPath(target, path, value) {
  const parts = path.split(".");
  let cursor = target;
  for (const key of parts.slice(0, -1)) {
    if (typeof cursor[key] !== "object" || cursor[key] === null) cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[parts.at(-1)] = value;
}

function dailyCardStylePicker(input, field) {
  if (!field.options.some(([key]) => key === input.value)) input.value = field.default;
  let saved = input.value;
  const options = [
    { key: "classic", title: "现有稀有度卡面", detail: "场景立绘 · 主题边饰 · 稀有度光效" },
    { key: "tcg", title: "TCG 收藏卡面", detail: "实体卡比例 · 金属包边 · 五档印刷材质" },
    { key: "yugioh", title: "游戏王收藏卡面", detail: "怪兽卡构图 · 立绘画窗 · 五档纸面与稀有工艺" },
  ];
  const hintId = `${input.id}-hint`;
  const picker = el("fieldset", { class: "card-style-picker", "aria-describedby": hintId });
  const choices = [];
  const dialogImage = el("img", { alt: "", decoding: "async" });
  const dialogTitle = el("h2", { id: `${input.id}-preview-title` });
  const closeButton = el("button", { type: "button", class: "icon-button", "aria-label": "关闭卡面预览", text: "×" });
  const dialog = el("dialog", { class: "card-style-dialog", "aria-labelledby": dialogTitle.id }, [
    el("div", { class: "card-style-dialog-head" }, [dialogTitle, closeButton]),
    dialogImage,
    el("p", { class: "card-style-dialog-note", text: "五档依次为普通、稀有、史诗、传说与闪光。实际卡面随宝可梦属性、立绘与抽取结果变化。" }),
  ]);
  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) dialog.close();
  });
  const sync = () => {
    for (const { key, radio, choice, badge } of choices) {
      radio.checked = key === input.value;
      choice.classList.toggle("selected", radio.checked);
      const active = key === saved;
      badge.textContent = active ? "使用中" : radio.checked ? "待保存" : "";
      badge.hidden = !active && !radio.checked;
      badge.classList.toggle("pending", radio.checked && !active);
    }
  };
  input.hidden = true;
  picker.append(el("legend", { text: field.label }), input);
  const gallery = el("div", { class: "card-style-options" });
  for (const option of options) {
    const radio = el("input", { type: "radio", name: `${input.id}-preview`, value: option.key, "aria-describedby": `${input.id}-${option.key}-detail` });
    const badge = el("span", { class: "card-style-badge" });
    const image = el("img", {
      src: `./assets/card-styles/${option.key}.jpg`,
      alt: `${option.title}的普通、稀有、史诗、传说、闪光五种卡面预览`,
      loading: "lazy", decoding: "async",
    });
    const fallback = el("span", { class: "card-style-fallback", hidden: true, text: "预览暂不可用，仍可选择此样式" });
    const openButton = el("button", { type: "button", class: "card-style-zoom", text: "查看大图", "aria-label": `查看${option.title}大图` });
    image.addEventListener("error", () => {
      image.hidden = true;
      fallback.hidden = false;
      openButton.dataset.previewUnavailable = "true";
      openButton.disabled = true;
    });
    const label = el("label", { class: "card-style-label" }, [
      el("span", { class: "card-style-heading" }, [radio, el("strong", { text: option.title }), badge]),
      el("span", { class: "card-style-art" }, [image, fallback]),
      el("span", { class: "card-style-description", id: `${input.id}-${option.key}-detail`, text: option.detail }),
    ]);
    const choice = el("div", { class: "card-style-option" }, [label, el("div", { class: "card-style-footer" }, [
      el("span", { text: "普通 / 稀有 / 史诗 / 传说 / 闪光" }), openButton,
    ])]);
    radio.addEventListener("input", () => {
      input.value = radio.value;
      sync();
    });
    openButton.addEventListener("click", () => {
      dialogTitle.textContent = option.title;
      dialogImage.src = image.src;
      dialogImage.alt = image.alt;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else window.open(image.src, "_blank", "noopener");
    });
    choices.push({ key: option.key, radio, choice, badge });
    gallery.append(choice);
  }
  input.addEventListener("input", sync);
  input.addEventListener("change", sync);
  picker.append(gallery, el("p", { id: hintId, class: "card-style-hint", text: `${field.hint} 选择后点击下方「保存配置」生效。` }), dialog);
  sync();
  return { element: picker, saved: () => { saved = input.value; sync(); } };
}

function setupSettings() {
  const form = document.getElementById("settingsForm");
  const grid = document.getElementById("settingsGrid");
  const status = document.getElementById("settingsStatus");
  const reload = document.getElementById("settingsReload");
  const inputs = new Map();
  let saving = false;
  let dirty = false;
  let cardStylePicker = null;
  let edits = 0;
  let loadSequence = 0;
  form.addEventListener("input", () => {
    edits += 1;
    dirty = true;
    status.textContent = "有尚未保存的修改";
    status.className = "form-status";
  });

  const build = (config) => {
    clear(grid);
    inputs.clear();
    cardStylePicker = null;
    for (const group of SETTINGS_FIELDS) {
      grid.append(el("div", { class: "group-title", text: group.group }));
      for (const field of group.fields) {
        const value = getPath(config, field.key);
        let input;
        if (field.type === "bool") {
          input = el("input", { type: "checkbox" });
          input.checked = Boolean(value);
        } else if (field.type === "select") {
          input = el("select", {}, field.options.map(([key, label]) => el("option", { value: key, text: label })));
          input.value = value ?? field.default;
        } else {
          input = el("input", {
            type: field.type === "number" ? "number" : "text",
            min: field.min,
            max: field.max,
            step: field.step,
            value: value === undefined || value === null ? "" : String(value),
          });
        }
        input.id = `setting-${field.key.replaceAll(".", "-")}`;
        inputs.set(field.key, { field, input });
        if (field.key === "daily_card_style") {
          cardStylePicker = dailyCardStylePicker(input, field);
          grid.append(cardStylePicker.element);
          continue;
        }
        grid.append(
          el("div", { class: `form-field${field.wide ? " wide" : ""}` }, [
            el("label", { text: field.label, for: input.id }),
            input,
            field.hint ? el("span", { class: "hint", text: field.hint }) : null,
          ]),
        );
      }
    }
  };

  const load = async () => {
    if (saving) return false;
    if (dirty && !(await confirmAction({ title: "放弃尚未保存的修改？", message: "重新读取已保存的设置，当前修改会被丢弃。", confirmLabel: "放弃修改" }))) return false;
    if (saving) return false;
    const sequence = ++loadSequence;
    const revision = edits;
    status.textContent = "";
    status.className = "form-status";
    const data = await api("/settings");
    // A slow read must not replace newer edits or a save that started later.
    if (saving || sequence !== loadSequence || edits !== revision) return false;
    build(data.config ?? {});
    dirty = false;
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (saving || !form.reportValidity()) return;
    saving = true;
    loadSequence += 1;
    // Freeze the submitted values until the response arrives, including preview
    // radios, so later edits cannot be mistaken for the saved configuration.
    const disabled = new Map(Array.from(form.querySelectorAll("input, select, button"), (control) => [control, control.disabled]));
    for (const control of disabled.keys()) control.disabled = true;
    form.setAttribute("aria-busy", "true");
    const patch = {};
    for (const [key, { field, input }] of inputs) {
      let value;
      if (field.type === "bool") value = input.checked;
      else if (field.type === "number") value = Number(input.value || 0);
      else value = input.value;
      setPath(patch, key, value);
    }
    status.textContent = "保存中…";
    status.className = "form-status";
    try {
      await apiPost("/settings/save", { config: patch });
      dirty = false;
      cardStylePicker?.saved();
      state.loaded.delete("overview");
      state.loaded.delete("rules");
      state.loaded.delete("dex");
      state.loaded.delete("members");
      status.textContent = "已保存并重新加载抽卡规则。";
      status.className = "form-status success";
      toast("配置已保存", "success");
    } catch (error) {
      status.textContent = error.message;
      status.className = "form-status error";
      toast(error.message, "error", 6000);
    } finally {
      saving = false;
      for (const [control, wasDisabled] of disabled) control.disabled = wasDisabled || control.dataset.previewUnavailable === "true";
      form.removeAttribute("aria-busy");
    }
  });

  reload.addEventListener("click", () => {
    load().catch((error) => toast(error.message, "error"));
  });

  return { load };
}

/* ================================================================= 启动 */

function setupSidebar() {
  const shell = document.getElementById("shell");
  const nav = document.getElementById("nav");
  const toggle = document.getElementById("sidebarToggle");
  const close = document.getElementById("sidebarClose");
  const backdrop = document.getElementById("sidebarBackdrop");
  const workspace = document.getElementById("workspace");
  const actions = document.querySelector(".topbar-actions");
  const skip = document.querySelector(".skip-link");
  const mobile = window.matchMedia("(max-width: 780px)");
  let collapsed = false;
  let mobileOpen = false;
  try { collapsed = localStorage.getItem("partner-console-sidebar") === "collapsed"; } catch { /* sandbox storage may be unavailable */ }
  for (const button of nav.querySelectorAll("button[data-view]")) {
    const label = button.textContent.trim();
    button.setAttribute("aria-label", label);
    button.title = label;
  }
  const update = () => {
    const open = mobile.matches && mobileOpen;
    const detailOpen = document.body.classList.contains("drawer-open");
    shell.classList.toggle("sidebar-collapsed", !mobile.matches && collapsed);
    shell.classList.toggle("mobile-nav-open", mobile.matches && mobileOpen);
    backdrop.hidden = !mobile.matches || !mobileOpen;
    document.body.classList.toggle("navigation-open", mobile.matches && mobileOpen);
    nav.inert = (mobile.matches && !mobileOpen) || detailOpen;
    workspace.inert = open;
    actions.inert = open;
    skip.inert = open;
    toggle.disabled = detailOpen;
    if (open) { nav.setAttribute("role", "dialog"); nav.setAttribute("aria-modal", "true"); }
    else { nav.removeAttribute("role"); nav.removeAttribute("aria-modal"); }
    if (mobile.matches && !mobileOpen) nav.setAttribute("aria-hidden", "true");
    else nav.removeAttribute("aria-hidden");
    const expanded = mobile.matches ? mobileOpen : !collapsed;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.title = expanded ? "收起导航" : "展开导航";
    toggle.setAttribute("aria-label", toggle.title);
  };
  const closeMobile = (restore = false) => {
    mobileOpen = false;
    update();
    if (restore) toggle.focus({ preventScroll: true });
  };
  const focusMobile = () => {
    if (mobile.matches && mobileOpen) (nav.querySelector("button.active") || close).focus({ preventScroll: true });
  };
  nav.addEventListener("transitionend", (event) => {
    if (event.target === nav && event.propertyName === "transform") focusMobile();
  });
  toggle.addEventListener("click", () => {
    if (document.body.classList.contains("drawer-open")) return;
    if (mobile.matches) mobileOpen = !mobileOpen;
    else {
      collapsed = !collapsed;
      try { localStorage.setItem("partner-console-sidebar", collapsed ? "collapsed" : "expanded"); } catch { /* keep current state in memory */ }
    }
    update();
    if (mobile.matches && mobileOpen) requestAnimationFrame(focusMobile);
  });
  backdrop.addEventListener("click", () => closeMobile(true));
  close.addEventListener("click", () => closeMobile(true));
  nav.addEventListener("click", (event) => {
    if (mobile.matches && event.target.closest("button[data-view]")) {
      closeMobile();
      document.getElementById("workspace").focus({ preventScroll: true });
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobileOpen) { event.preventDefault(); closeMobile(true); }
    if (event.key === "Tab" && mobile.matches && mobileOpen) {
      const buttons = [...nav.querySelectorAll("button:not(:disabled)")];
      const first = buttons[0];
      const last = buttons.at(-1);
      if (!nav.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first)?.focus();
      }
    }
  });
  document.addEventListener("partner-drawer-change", update);
  mobile.addEventListener("change", () => { mobileOpen = false; update(); });
  update();
}

async function boot() {
  paintIcons();
  applyTheme(currentTheme());
  for (const button of document.querySelectorAll("#themeSwitch button[data-theme]")) {
    button.addEventListener("click", () => applyTheme(button.dataset.theme));
  }
  setupSidebar();

  await ready();

  state.views = {
    overview: setupOverview(),
    dex: setupDex(),
    members: setupMembers(),
    rules: setupRules(),
    settings: setupSettings(),
  };

  for (const button of document.querySelectorAll(".nav-item")) {
    button.addEventListener("click", () => showView(button.dataset.view));
  }
  document.getElementById("refreshButton").addEventListener("click", refreshView);
  for (const button of document.querySelectorAll("[data-go]")) button.addEventListener("click", () => showView(button.dataset.go));
  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || document.body.classList.contains("drawer-open") || document.querySelector(".card-style-dialog[open]")) return;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)) return;
    event.preventDefault();
    showView("dex");
    document.getElementById("dexSearch").focus();
  });

  const initial = location.hash.slice(1);
  await showView(state.views[initial] ? initial : "overview");
  document.getElementById("shell").setAttribute("aria-busy", "false");
}

boot().catch((error) => {
  document.getElementById("shell")?.setAttribute("aria-busy", "false");
  const notice = document.getElementById("pageNotice");
  notice.hidden = false;
  notice.textContent = `页面初始化失败：${error.message}`;
  toast(`页面初始化失败：${error.message}`, "error", 8000);
});
