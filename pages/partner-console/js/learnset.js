import { clear, el, typeStyle } from "./core.js";

const SOURCES = { level_up: "升级", machine: "学习器", egg: "遗传" };

/** Normalize old and current API shapes, merging a move learned by several methods. */
export function normalizeLearnset(learnset = {}, details = {}) {
  const moves = new Map();
  for (const [source, label] of Object.entries(SOURCES)) {
    for (const entry of learnset[source] ?? []) {
      const [name, level] = Array.isArray(entry) ? entry : [entry, null];
      if (typeof name !== "string" || !name.trim()) continue;
      const key = name.trim();
      const move = moves.get(key) ?? { ...details[key], name: key, sources: [] };
      const parsed = Number(level);
      const known = level !== null && level !== undefined && level !== "" && Number.isFinite(parsed) && parsed > 0;
      if (!move.sources.some((item) => item.key === source && item.level === (known ? parsed : null))) {
        move.sources.push({ key: source, label, level: known ? parsed : null });
      }
      moves.set(key, move);
    }
  }
  return [...moves.values()];
}

export function filterLearnset(moves, source = "all", query = "") {
  const needle = query.trim().toLocaleLowerCase();
  return moves.filter((move) =>
    (source === "all" || move.sources.some((item) => item.key === source)) &&
    (!needle || [move.name, move.name_en, move.type, move.category].filter(Boolean).join(" ").toLocaleLowerCase().includes(needle)),
  );
}

/** Compact, searchable reference; descriptions stay next to their selected move. */
export function learnsetExplorer(data) {
  const moves = normalizeLearnset(data.learnset, data.move_details);
  const box = el("div", { class: "learnset-explorer" });
  const search = el("input", { type: "search", placeholder: "搜索名称、英文名或属性", "aria-label": "搜索可学招式" });
  const tabs = el("div", { class: "learnset-tabs", role: "group", "aria-label": "招式学习来源" });
  const meta = el("div", { class: "learnset-meta", "aria-live": "polite" });
  const list = el("div", { class: "learnset-list" });
  const pager = el("div", { class: "pager learnset-pager", "aria-label": "招式分页" });
  let source = "all";
  let page = 1;
  const pageSize = 12;

  const render = () => {
    const filtered = filterLearnset(moves, source, search.value);
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    page = Math.max(1, Math.min(page, pages));
    for (const button of tabs.children) button.setAttribute("aria-pressed", String(button.dataset.source === source));
    meta.textContent = `${filtered.length} 个招式${pages > 1 ? ` · 第 ${page}/${pages} 页` : ""} · 点击招式查看效果`;
    clear(list);
    if (!filtered.length) list.append(el("div", { class: "empty-state", text: moves.length ? "没有符合条件的招式，试试其他名称或来源。" : "本地资料未收录此宝可梦的学习表。" }));
    for (const move of filtered.slice((page - 1) * pageSize, page * pageSize)) {
      const entry = el("details", { class: "learnset-move" });
      const title = el("summary", { class: "learnset-summary" }, [
        el("span", { class: "learnset-name", text: move.name }),
        el("span", { class: "learnset-badges" }, [
          move.type ? el("span", { class: "chip", style: typeStyle(move.type), text: move.type }) : null,
          move.category ? el("span", { class: "learnset-category", text: move.category }) : null,
        ]),
        el("span", { class: "learnset-power", text: `威力 ${move.power || "—"}` }),
      ]);
      const info = el("div", { class: "learnset-info" }, [
        el("div", { class: "learnset-facts" }, [
          move.name_en ? el("span", { text: move.name_en }) : null,
          el("span", { text: `命中 ${move.accuracy || "—"}` }),
          el("span", { text: `PP ${move.pp || "—"}` }),
        ]),
        el("div", { class: "learnset-methods", text: move.sources.map((item) => item.key === "level_up" ? (item.level === null ? "升级 · 等级未知" : `升级 · Lv.${item.level}`) : item.label).join(" / ") }),
        el("p", { class: "text-block", text: move.description || "本地资料暂缺此招式的效果说明。" }),
      ]);
      if (move.additional_effect && move.additional_effect !== move.description) {
        info.append(el("details", { class: "learnset-extra" }, [
          el("summary", { text: "详细效果与机制" }),
          el("p", { class: "text-block", text: move.additional_effect }),
        ]));
      }
      entry.append(title, info);
      list.append(entry);
    }
    clear(pager);
    if (pages > 1) {
      for (const [label, next, disabled] of [["上一页", page - 1, page <= 1], ["下一页", page + 1, page >= pages]]) {
        pager.append(el("button", { type: "button", text: label, disabled, onclick: (event) => {
          page = next;
          render();
          // Re-rendering removes the clicked node; preserve keyboard focus inside the dialog.
          const replacement = [...pager.children].find((button) => button.textContent === event.currentTarget.textContent && !button.disabled);
          (replacement ?? search).focus({ preventScroll: true });
          list.scrollIntoView({ block: "nearest", behavior: "instant" });
        } }));
      }
    }
  };
  for (const [key, label] of [["all", "全部"], ...Object.entries(SOURCES)]) {
    const count = key === "all" ? moves.length : moves.filter((move) => move.sources.some((item) => item.key === key)).length;
    tabs.append(el("button", { type: "button", dataset: { source: key }, text: `${label} ${count}`, disabled: key !== "all" && count === 0, onclick: () => { source = key; page = 1; render(); } }));
  }
  search.addEventListener("input", () => { page = 1; render(); });
  box.append(tabs, search, meta, list, pager, el("p", { class: "learnset-note", text: data.learnset_note || "本地资料中的学习表；招式可用性会随游戏版本变化。" }));
  render();
  return box;
}
