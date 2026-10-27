/**
 * 管理台视图层。
 *
 * 结构：每个视图一个 initializer，返回 `{ load, refresh }`；
 * 切换视图时懒加载，刷新按钮重跑当前视图的 load。
 */

import {
  api,
  apiPost,
  applyTheme,
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
} from "./js/core.js";

const state = {
  view: "overview",
  loaded: new Set(),
  views: {},
  dexTypes: new Set(),
  dexGens: new Set(),
};

/* ================================================================= 路由 */

function showView(name) {
  if (!state.views[name]) return;
  state.view = name;
  for (const panel of document.querySelectorAll("[data-panel]")) {
    panel.hidden = panel.dataset.panel !== name;
  }
  for (const button of document.querySelectorAll(".nav-item")) {
    button.classList.toggle("active", button.dataset.view === name);
  }
  if (location.hash.slice(1) !== name) {
    history.replaceState(null, "", `#${name}`);
  }
  const view = state.views[name];
  if (!state.loaded.has(name)) {
    state.loaded.add(name);
    view.load();
  }
}

async function refreshView() {
  const view = state.views[state.view];
  if (!view) return;
  try {
    await (view.refresh ?? view.load)();
  } catch (error) {
    toast(error.message || "刷新失败", "error");
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
    if (health.load_failed) {
      toast("数据文件损坏且备份不可用，插件已停止写入，请检查服务器文件。", "error", 8000);
    }
  };

  return { load };
}

function rarityRow(rarity, percent, meta) {
  const width = Math.max(1.5, Math.min(100, percent));
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

  const collectOptions = (items) => {
    if (optionsReady) return;
    optionsReady = true;
    for (const item of items) {
      for (const type of item.types ?? []) state.dexTypes.add(type);
      if (item.generation) state.dexGens.add(item.generation);
    }
    for (const type of [...state.dexTypes].sort()) {
      typeSel.append(el("option", { value: type, text: type }));
    }
    for (const gen of [...state.dexGens].sort((a, b) => Number(a) - Number(b))) {
      genSel.append(el("option", { value: gen, text: `第 ${gen} 世代` }));
    }
  };

  const renderPager = () => {
    clear(pager);
    const pages = Math.max(1, Math.ceil(page.total / page.size));
    if (pages <= 1) return;
    const go = (index) => {
      page.index = index;
      load();
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
    const data = await api("/dex", {
      q: search.value.trim(),
      type: typeSel.value,
      gen: genSel.value,
      rarity: raritySel.value,
      forms: formsBox.checked ? 1 : 0,
      page: page.index,
      size: page.size,
    });
    page.total = data.total ?? 0;
    collectOptions(data.items ?? []);
    clear(grid);
    if (!data.items?.length) {
      grid.append(el("div", { class: "empty-state", text: "没有符合条件的宝可梦。" }));
    }
    for (const item of data.items ?? []) {
      const card = el(
        "button",
        {
          type: "button",
          class: `dex-card${selected === item.id_raw ? " selected" : ""}`,
          dataset: { rarity: item.rarity || "C" },
          onclick: () => {
            lastTrigger = card;
            selected = item.id_raw;
            for (const node of grid.children) node.classList?.remove("selected");
            card.classList.add("selected");
            loadDetail(item).catch((error) => {
              const content = mountDrawer();
              content.append(
                el("div", { class: "empty-state", text: error.message }),
              );
              toast(error.message, "error", 6000);
            });
          },
        },
        [
          el("span", { class: "dex-id", text: `#${item.id_raw}` }),
          el("span", { class: "dex-name", text: item.name_zh + (item.form_name ? `（${item.form_name}）` : "") }),
          el("span", { class: "dex-sub", text: item.name_en || item.category }),
          el("div", { class: "chips" }, [
            ...(item.types ?? []).map((type) =>
              el("span", {
                class: "chip",
                style: `background:${typeColor(type)}`,
                text: type,
              }),
            ),
            item.rarity
              ? el("span", {
                  class: `chip rarity-${item.rarity}`,
                  text: item.rarity,
                })
              : null,
          ]),
        ],
      );
      grid.append(card);
    }
    meta.textContent = `共 ${num(page.total)} 条 · 第 ${page.index}/${Math.max(1, Math.ceil(page.total / page.size))} 页`;
    renderPager();
  };

  const closeDetail = () => {
    detail.hidden = true;
    backdrop.hidden = true;
    document.body.classList.remove("drawer-open");
    selected = null;
    for (const node of grid.children) node.classList?.remove("selected");
    lastTrigger?.focus?.({ preventScroll: true });
    lastTrigger = null;
  };

  const mountDrawer = () => {
    detail.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("drawer-open");
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
    detail.append(close, content);
    paintIcons(detail);
    detail.focus({ preventScroll: true });
    return content;
  };

  const loadDetail = async (item) => {
    const content = mountDrawer();
    content.append(el("p", { class: "placeholder", text: "加载中…" }));
    const data = await api("/dex/detail", { key: item.id_raw });
    const row = data.row ?? {};
    clear(content);
    detail.setAttribute("aria-label", `${row.name_zh || item.name_zh} 图鉴详情`);

    content.append(
      el("div", { class: "detail-head" }, [
        el("div", {}, [
          el("div", { class: "detail-title", text: row.name_zh || item.name_zh }),
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

    if (data.artwork) {
      content.append(
        el("figure", { class: "detail-art" }, [
          el("img", {
            src: data.artwork,
            alt: `${row.name_zh || item.name_zh} 官方立绘`,
            loading: "eager",
            decoding: "async",
          }),
        ]),
      );
    }

    content.append(
      el("div", { class: "chips" }, [
        ...(row.types || "")
          .split("/")
          .filter(Boolean)
          .map((type) =>
            el("span", { class: "chip", style: `background:${typeColor(type)}`, text: type }),
          ),
        el("span", { class: "chip ghost", text: `#${row.id_raw}` }),
        el("span", { class: "chip ghost", text: `第 ${row.generation} 世代` }),
        el("span", { class: "chip ghost", text: row.category }),
      ]),
    );

    const stats = data.stats ?? {};
    const maxStat = Math.max(180, ...Object.values(stats).map(Number));
    const bars = el("div", { class: "stat-bars" });
    for (const [label, value] of Object.entries(stats)) {
      bars.append(
        el("div", { class: "stat-bar" }, [
          el("span", { text: label }),
          el("div", { class: "track" }, [
            el("div", {
              class: "fill",
              style: `width:${Math.min(100, (Number(value) / maxStat) * 100)}%`,
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

    content.append(
      section("基础信息", [
        el("div", {
          class: "text-block",
          text: [
            `身高 ${row.height || "—"}｜体重 ${row.weight || "—"}`,
            `捕获度 ${row.catch_rate || "—"}`,
            `蛋群 ${row.egg_groups || "—"}`,
            `性别 ${row.gender_ratio || "—"}`,
            row.evolution ? `进化：${row.evolution}` : "",
          ]
            .filter(Boolean)
            .join("\n"),
        }),
      ]),
    );

    const learn = data.learnset ?? {};
    if (learn.level_up?.length) {
      content.append(
        section("升级招式", [
          el(
            "div",
            { class: "move-list" },
            learn.level_up.map(([name, level]) =>
              el("span", { class: "move", text: level ? `${name} ${level}` : name }),
            ),
          ),
        ]),
      );
    }
    for (const [label, moves] of [
      ["招式学习器", learn.machine],
      ["遗传招式", learn.egg],
    ]) {
      if (!moves?.length) continue;
      content.append(
        section(label, [
          el(
            "div",
            { class: "move-list" },
            moves.map((name) => el("span", { class: "move", text: name })),
          ),
        ]),
      );
    }

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
  backdrop.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !detail.hidden) closeDetail();
  });

  return {
    load: () =>
      load().catch((error) => {
        toast(error.message, "error");
        clear(grid);
        grid.append(el("div", { class: "empty-state", text: error.message }));
      }),
  };
}

function section(label, children) {
  return el("div", {}, [el("div", { class: "section-label", text: label }), ...children]);
}

function clampedText(value) {
  const box = el("div", { class: "text-block clamped", text: value });
  const button = el("button", {
    type: "button",
    class: "expand-button",
    text: "展开",
    onclick: () => {
      const open = box.classList.toggle("clamped");
      button.textContent = open ? "展开" : "收起";
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
    scopeSel.value = current;
    sessionsLoaded = true;
  };

  const renderPager = () => {
    clear(pager);
    const pages = Math.max(1, Math.ceil(page.total / page.size));
    if (pages <= 1) return;
    const go = (index) => {
      page.index = index;
      load();
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
    try {
      await apiPost("/members/update", body);
      toast(okMessage, "success");
      await load();
      await loadSessions(true);
    } catch (error) {
      toast(error.message, "error");
    }
  };

  const removeMember = async (scope, userId, name) => {
    if (!confirm(`确定删除 ${name} 在 ${scope} 的全部宝可梦数据？此操作不可撤销。`)) return;
    try {
      await apiPost("/sessions/delete", { scope, user_id: userId });
      toast("已删除该成员数据", "success");
      await load();
      await loadSessions(true);
    } catch (error) {
      toast(error.message, "error");
    }
  };

  const load = async () => {
    const data = await api("/members", {
      scope: scopeSel.value,
      q: search.value.trim(),
      page: page.index,
      size: page.size,
    });
    page.total = data.total ?? 0;
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
    if (!confirm(`重置 ${scope} 全部成员今天的抽取次数？`)) return;
    try {
      const result = await apiPost("/sessions/reset", { scope });
      toast(`已重置 ${result.changed ?? 0} 位成员`, "success");
      await load();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  return {
    load: async () => {
      await loadSessions();
      await load();
    },
    refresh: async () => {
      await loadSessions(true);
      await load();
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

function setupSettings() {
  const form = document.getElementById("settingsForm");
  const grid = document.getElementById("settingsGrid");
  const status = document.getElementById("settingsStatus");
  const reload = document.getElementById("settingsReload");
  const inputs = new Map();

  const build = (config) => {
    clear(grid);
    inputs.clear();
    for (const group of SETTINGS_FIELDS) {
      grid.append(el("div", { class: "group-title", text: group.group }));
      for (const field of group.fields) {
        const value = getPath(config, field.key);
        let input;
        if (field.type === "bool") {
          input = el("input", { type: "checkbox" });
          input.checked = Boolean(value);
        } else {
          input = el("input", {
            type: field.type === "number" ? "number" : "text",
            min: field.min,
            max: field.max,
            step: field.step,
            value: value === undefined || value === null ? "" : String(value),
          });
        }
        inputs.set(field.key, { field, input });
        grid.append(
          el("div", { class: `form-field${field.wide ? " wide" : ""}` }, [
            el("label", { text: field.label }),
            input,
            field.hint ? el("span", { class: "hint", text: field.hint }) : null,
          ]),
        );
      }
    }
  };

  const load = async () => {
    status.textContent = "";
    status.className = "form-status";
    const data = await api("/settings");
    build(data.config ?? {});
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
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
      status.textContent = "已保存并重新加载抽卡规则。";
      status.className = "form-status success";
      toast("配置已保存", "success");
    } catch (error) {
      status.textContent = error.message;
      status.className = "form-status error";
      toast(error.message, "error", 6000);
    }
  });

  reload.addEventListener("click", () => {
    load().catch((error) => toast(error.message, "error"));
  });

  return { load };
}

/* ================================================================= 启动 */

async function boot() {
  paintIcons();
  applyTheme(currentTheme());
  for (const button of document.querySelectorAll("[data-theme]")) {
    button.addEventListener("click", () => applyTheme(button.dataset.theme));
  }

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

  const initial = location.hash.slice(1);
  showView(state.views[initial] ? initial : "overview");
  document.getElementById("shell").setAttribute("aria-busy", "false");
}

boot().catch((error) => {
  document.getElementById("shell")?.setAttribute("aria-busy", "false");
  toast(`页面初始化失败：${error.message}`, "error", 8000);
});
