/**
 * 页面基础设施：Bridge 调用、图标、DOM 与格式化工具。
 *
 * 约定：
 * - 所有后端调用都经过 `api()`，统一处理 `ok:false` 与网络异常；
 * - 图标用内联 SVG data URI + mask，随文字颜色变化，无外部依赖；
 * - 不引入任何框架，保持零构建。
 */

export const bridge = window.AstrBotPluginPage;

let context = null;

export async function ready() {
  if (context) return context;
  if (!bridge?.ready || !bridge?.apiGet || !bridge?.apiPost) {
    throw new Error("请从 AstrBot Dashboard 的插件页面打开此管理台。");
  }
  context = (await bridge.ready()) ?? {};
  return context;
}

function bridgeEndpoint(path) {
  const endpoint = String(path ?? "").replace(/^\/+/, "");
  if (!endpoint) throw new Error("Bridge endpoint 不能为空");
  if (/[?:#\\]/.test(endpoint) || endpoint.split("/").some((part) => part === "." || part === "..")) {
    throw new Error(`Bridge endpoint 格式无效：${endpoint}`);
  }
  return endpoint;
}

export async function api(path, params) {
  const query = {};
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    query[key] = value;
  }
  let payload;
  try {
    payload = await bridge.apiGet(bridgeEndpoint(path), query);
  } catch (error) {
    throw new Error(`请求失败：${error?.message || error}`);
  }
  if (payload && payload.ok === false) {
    throw new Error(payload.error || "请求被拒绝");
  }
  return payload ?? {};
}

export async function apiPost(path, body) {
  let payload;
  try {
    payload = await bridge.apiPost(bridgeEndpoint(path), body ?? {});
  } catch (error) {
    throw new Error(`请求失败：${error?.message || error}`);
  }
  if (payload && payload.ok === false) {
    throw new Error(payload.error || "请求被拒绝");
  }
  return payload ?? {};
}

/* ------------------------------------------------------------------ 图标 */

const ICONS = {
  gauge:
    '<path d="M12 3a9 9 0 1 0 9 9h-2a7 7 0 1 1-7-7zm0 0v7l5 3" fill="none" stroke="black" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  book:
    '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5zM4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5" fill="none" stroke="black" stroke-width="1.8" stroke-linejoin="round"/>',
  users:
    '<path d="M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm7.5 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM2.5 20c0-3.3 2.9-6 6.5-6s6.5 2.7 6.5 6M15.5 14.6c3.1.3 5.5 2.7 5.5 5.4" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  sliders:
    '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round"/><circle cx="16" cy="6" r="2.2" fill="black"/><circle cx="10" cy="12" r="2.2" fill="black"/><circle cx="18" cy="18" r="2.2" fill="black"/>',
  wrench:
    '<path d="M14.7 6.3a4.4 4.4 0 0 0 5.6 5.6l-8.6 8.6a2.6 2.6 0 0 1-3.7-3.7zM14.7 6.3 12 3.6a4.6 4.6 0 0 0-5.6 6.2l3.1 3.1" fill="none" stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  refresh:
    '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" fill="none" stroke="black" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
  x:
    '<path d="M6 6l12 12M18 6 6 18" fill="none" stroke="black" stroke-width="2" stroke-linecap="round"/>',
};

export function paintIcons(root = document) {
  for (const node of root.querySelectorAll("[data-icon]")) {
    const name = node.dataset.icon;
    const svg = ICONS[name];
    if (!svg) continue;
    const encoded = encodeURIComponent(
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">${svg}</svg>`,
    );
    node.style.webkitMaskImage = `url("data:image/svg+xml,${encoded}")`;
    node.style.maskImage = `url("data:image/svg+xml,${encoded}")`;
  }
}

/* --------------------------------------------------------------- DOM 工具 */

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function text(node, value) {
  node.textContent = value ?? "";
}

/* ------------------------------------------------------------------ 格式化 */

export function num(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString("zh-CN");
}

export function bytes(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export const RARITY_LABEL = { C: "普通", R: "稀有", SR: "史诗", SSR: "传说" };

export const RARITY_COLOR = {
  C: "var(--pk-rarity-c)",
  R: "var(--pk-rarity-r)",
  SR: "var(--pk-rarity-sr)",
  SSR: "var(--pk-rarity-ssr)",
};

const TYPE_COLOR = {
  一般: "#9aa19f",
  火: "#e62828",
  水: "#2980ef",
  草: "#3fa129",
  电: "#e8b800",
  冰: "#3dcef3",
  格斗: "#ff8000",
  毒: "#9141cb",
  地面: "#915121",
  飞行: "#81b9ef",
  超能力: "#ef4179",
  虫: "#91a119",
  岩石: "#afa981",
  幽灵: "#704170",
  龙: "#5060e1",
  恶: "#624d4e",
  钢: "#a1a1a1",
  妖精: "#ef70ef",
};

export function typeColor(type) {
  return TYPE_COLOR[type] ?? "#64748b";
}

/** 属性色有浅有深，按亮度选择文字色，保证电、冰、飞行等徽章可读。 */
export function typeStyle(type) {
  const color = typeColor(type);
  const rgb = [1, 3, 5].map((at) => parseInt(color.slice(at, at + 2), 16));
  const luminance = rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114;
  return `background:${color};color:${luminance > 155 ? "#202a27" : "#fff"}`;
}

/* --------------------------------------------------------------- Toast */

let toastBox = null;

export function toast(message, kind = "info", timeout = 3200) {
  toastBox ??= document.getElementById("toasts");
  if (!toastBox) return;
  const node = el("div", { class: `toast ${kind}`, role: "status" }, message);
  toastBox.append(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(4px)";
    node.style.transition = "opacity 160ms, transform 160ms";
    setTimeout(() => node.remove(), 200);
  }, timeout);
}

/* --------------------------------------------------------------- 主题 */

const THEME_KEY = "partner-console-theme";

export function currentTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || "auto";
  } catch {
    return "auto";
  }
}

export function applyTheme(theme) {
  const value = ["auto", "light", "dark"].includes(theme) ? theme : "auto";
  document.documentElement.dataset.partnerTheme = value;
  if (document.body) document.body.dataset.partnerTheme = value;
  document.documentElement.style.colorScheme =
    value === "auto" ? "light dark" : value;
  for (const button of document.querySelectorAll("[data-theme]")) {
    button.setAttribute("aria-pressed", String(button.dataset.theme === value));
  }
  try {
    localStorage.setItem(THEME_KEY, value);
  } catch {
    /* localStorage 不可用时忽略 */
  }
}
