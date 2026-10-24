/*
 * EnOcean Internal Chip: the Home Assistant panel.
 *
 * A plain web component, no build step. It follows the Home Assistant theme
 * through its CSS variables, talks to the integration through hass.callApi,
 * and only ever redraws the part of the page that changed: lists, counters
 * and pills are updated in place, scroll positions and open dialogs stay.
 */

const DOMAIN = "enocean_internal_chip";
const PREFS_KEY = "enocean_internal_chip_panel";
const PAGE_SIZES = [50, 100, 200, 500];
const PHONE = window.matchMedia("(max-width: 767px)");

const TABS = [
  { key: "devices", label: "Devices", icon: "mdi:devices" },
  { key: "nearby", label: "Nearby", icon: "mdi:radar" },
  { key: "telegrams", label: "Telegrams", icon: "mdi:format-list-text" },
  { key: "types", label: "Device types", short: "Types", icon: "mdi:shape-outline" },
  { key: "gateway", label: "Gateway", icon: "mdi:router-wireless" },
];

const PAIRING_TEXT = {
  none: "Listens only, nothing to pair",
  ute: "Pairs on request (UTE teach-in)",
  rps: "Own sender ID, rocker teach-in",
  "4bs": "Own sender ID, 4BS teach-in",
};

const RORG_CHOICES = [
  ["F6", "F6 · RPS (rocker, push button)"],
  ["D5", "D5 · 1BS (contact)"],
  ["A5", "A5 · 4BS (sensors)"],
  ["D2", "D2 · VLD (actuators, buttons)"],
  ["D4", "D4 · UTE (teach-in)"],
];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const compactId = (id) => String(id || "").replace(/:/g, "");

const errorText = (err) => err?.body?.message || err?.message || err?.error || String(err);

function ago(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

function clock(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const today = new Date();
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  if (date.toDateString() === today.toDateString()) return time;
  return `${date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${time}`;
}

function dateText(iso) {
  if (!iso) return "unknown";
  return new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : many || `${one}s`}`;
}

function signal(dbm) {
  if (dbm === null || dbm === undefined) return `<span class="signal none" title="No signal reading"></span>`;
  const level = dbm >= -70 ? 3 : dbm >= -85 ? 2 : 1;
  const word = ["", "weak", "fair", "strong"][level];
  return `<span class="signal s${level}" title="${dbm} dBm, ${word} signal" aria-label="${dbm} dBm, ${word} signal"><i></i><i></i><i></i></span>`;
}

function pill(text, kind = "", icon = "") {
  return `<span class="pill ${kind}">${icon ? `<ha-icon icon="${icon}"></ha-icon>` : ""}${esc(text)}</span>`;
}

function icon(name) {
  return `<ha-icon icon="${esc(name)}"></ha-icon>`;
}

const STYLE = `
:host {
  --eic-bg: var(--primary-background-color, #f5f6f8);
  --eic-card: var(--card-background-color, #fff);
  --eic-text: var(--primary-text-color, #1c1f24);
  --eic-muted: var(--secondary-text-color, #69707a);
  --eic-line: var(--divider-color, rgba(0, 0, 0, 0.12));
  --eic-accent: var(--primary-color, #0b8a8f);
  --eic-on-accent: var(--text-primary-color, #fff);
  --eic-ok: var(--success-color, #2e9d5b);
  --eic-warn: var(--warning-color, #e39a17);
  --eic-bad: var(--error-color, #d24343);
  --eic-radius: var(--ha-card-border-radius, 14px);
  --eic-shadow: var(--ha-card-box-shadow, 0 1px 2px rgba(0, 0, 0, 0.06));
  --eic-head-bg: var(--app-header-background-color, var(--eic-accent));
  --eic-head-text: var(--app-header-text-color, var(--eic-on-accent));
  --eic-soft: color-mix(in srgb, var(--eic-accent) 10%, transparent);
  display: block;
  height: 100%;
  color: var(--eic-text);
  background: var(--eic-bg);
  font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, system-ui, sans-serif));
  -webkit-font-smoothing: antialiased;
}
* { box-sizing: border-box; }
.app { display: flex; flex-direction: column; height: 100%; min-height: 100%; }
header.bar {
  display: flex; align-items: center; gap: 12px;
  min-height: 56px; padding: 0 16px;
  background: var(--eic-head-bg); color: var(--eic-head-text);
}
.bar .title { display: flex; align-items: baseline; gap: 8px; flex: 1; min-width: 0; }
.bar .name { font-size: 20px; font-weight: 500; }
.bar .sub { font-size: 14px; opacity: 0.8; white-space: nowrap; }
.bar .pills { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.bar .pill { background: rgba(255, 255, 255, 0.18); color: inherit; border-color: transparent; }
.bar .pill.ok::before, .bar .pill.bad::before, .bar .pill.warn::before {
  content: ""; width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px;
}
.bar .pill.ok::before { background: #6be59a; }
.bar .pill.bad::before { background: #ff8a80; }
.bar .pill.warn::before { background: #ffd166; }
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; margin-left: -10px; border: 0; border-radius: 50%;
  background: transparent; color: inherit; cursor: pointer;
}
.icon-btn:hover { background: rgba(127, 127, 127, 0.14); }
.icon-btn.menu { display: none; }
:host([narrow]) .icon-btn.menu { display: inline-flex; }
.pill .short { display: none; }

nav.tabs {
  display: flex; gap: 4px; padding: 8px 16px 0;
  background: var(--eic-card); border-bottom: 1px solid var(--eic-line);
  overflow-x: auto; scrollbar-width: none;
}
nav.tabs button {
  display: inline-flex; align-items: center; gap: 8px;
  min-height: 44px; padding: 0 16px; border: 0; border-bottom: 3px solid transparent;
  background: none; color: var(--eic-muted); font: inherit; font-size: 15px; font-weight: 500;
  cursor: pointer; white-space: nowrap;
}
nav.tabs button[aria-selected="true"] { color: var(--eic-accent); border-bottom-color: var(--eic-accent); }
nav.tabs button .short { display: none; }
nav.tabs ha-icon { --mdc-icon-size: 20px; }

main { flex: 1; overflow-y: auto; overscroll-behavior: contain; }
section { padding: 24px; max-width: 1280px; margin: 0 auto; }
section[hidden] { display: none; }
.intro { margin: 0 0 16px; color: var(--eic-muted); font-size: 15px; line-height: 1.5; max-width: 780px; }
h2 { font-size: 22px; font-weight: 500; margin: 0 0 8px; }
h3 { font-size: 16px; font-weight: 600; margin: 0; }

.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.toolbar .grow { flex: 1; }
.search {
  display: flex; align-items: center; gap: 8px; flex: 1; min-width: 220px;
  padding: 0 12px; border: 1px solid var(--eic-line); border-radius: 23px;
  background: var(--eic-card); color: var(--eic-muted);
}
.search input[type="search"] {
  flex: 1; min-width: 0; min-height: 44px; padding: 0; border: 0; outline: 0; border-radius: 0;
  background: none; color: var(--eic-text); font: inherit; font-size: 16px;
}
.search:focus-within { border-color: var(--eic-accent); }
.field.type-category-select { display: none; }
.count { margin: 0 0 12px; color: var(--eic-muted); font-size: 14px; }

.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  min-height: 44px; padding: 0 16px; border-radius: 22px;
  border: 1px solid var(--eic-line); background: var(--eic-card); color: var(--eic-text);
  font: inherit; font-size: 15px; font-weight: 500; cursor: pointer; text-decoration: none; white-space: nowrap;
}
.btn:hover { background: var(--eic-soft); }
.btn ha-icon { --mdc-icon-size: 18px; }
.btn.primary { background: var(--eic-accent); border-color: var(--eic-accent); color: var(--eic-on-accent); }
.btn.primary:hover { filter: brightness(1.08); }
.btn.ghost { border-color: transparent; background: transparent; color: var(--eic-accent); }
.btn.danger { background: var(--eic-bad); border-color: var(--eic-bad); color: #fff; }
.btn.danger-outline { border-color: color-mix(in srgb, var(--eic-bad) 60%, transparent); color: var(--eic-bad); background: transparent; }
.btn:disabled { opacity: 0.55; cursor: default; filter: none; }
.btn.busy { position: relative; color: transparent !important; }
.btn.busy::after {
  content: ""; position: absolute; width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid var(--eic-muted); border-top-color: transparent; animation: spin 0.8s linear infinite;
}
.btn.primary.busy::after, .btn.danger.busy::after { border-color: #fff; border-top-color: transparent; }
@keyframes spin { to { transform: rotate(360deg); } }

.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px; min-height: 44px; padding: 0 14px;
  border: 1px solid var(--eic-line); border-radius: 22px; background: var(--eic-card);
  color: var(--eic-text); font: inherit; font-size: 14px; cursor: pointer;
}
.chip[aria-pressed="true"] { background: var(--eic-soft); border-color: var(--eic-accent); color: var(--eic-accent); font-weight: 600; }
.chip .x { font-size: 18px; line-height: 1; opacity: 0.7; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr)); gap: 16px; }
.grid > * { min-width: 0; }
.card {
  position: relative; display: flex; flex-direction: column; gap: 12px;
  padding: 16px; border-radius: var(--eic-radius); background: var(--eic-card);
  border: 1px solid var(--eic-line); box-shadow: var(--eic-shadow);
}
.card.accent { border-top: 4px solid var(--cat, var(--eic-accent)); }
.card-head { display: flex; align-items: center; gap: 12px; }
.head-text { flex: 1; min-width: 0; }
.head-text h3 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.head-text p { margin: 2px 0 0; }
.avatar {
  flex: none; display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 12px;
  background: color-mix(in srgb, var(--cat, var(--eic-accent)) 14%, transparent); color: var(--cat, var(--eic-accent));
}
.muted { color: var(--eic-muted); font-size: 14px; }
.mono { font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace); font-size: 13px; }
.facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin: 0; }
.facts div { min-width: 0; }
.facts dt { font-size: 12px; color: var(--eic-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.facts dd { margin: 2px 0 0; font-size: 15px; overflow-wrap: anywhere; }
.pills-row { display: flex; flex-wrap: wrap; gap: 6px; }
.pills-row:empty { display: none; }
.pill {
  display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 12px;
  font-size: 13px; font-weight: 500; border: 1px solid var(--eic-line); color: var(--eic-muted); background: transparent;
}
.pill ha-icon { --mdc-icon-size: 14px; }
.pill.ok { color: var(--eic-ok); border-color: color-mix(in srgb, var(--eic-ok) 40%, transparent); }
.pill.warn { color: var(--eic-warn); border-color: color-mix(in srgb, var(--eic-warn) 50%, transparent); }
.pill.bad { color: var(--eic-bad); border-color: color-mix(in srgb, var(--eic-bad) 50%, transparent); }
.pill.accent { color: var(--eic-accent); border-color: color-mix(in srgb, var(--eic-accent) 45%, transparent); background: var(--eic-soft); }
.card-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: auto; }
.card-note { margin: 0; padding: 10px 12px; border-radius: 10px; background: var(--eic-soft); font-size: 14px; line-height: 1.45; }
.card-note:empty { display: none; }
.card-note.bad { background: color-mix(in srgb, var(--eic-bad) 10%, transparent); }
.summary { margin: 0; font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }
.card.is-new { border-color: var(--eic-accent); box-shadow: 0 0 0 1px var(--eic-accent); }

.signal { display: inline-flex; align-items: flex-end; gap: 2px; height: 16px; flex: none; }
.signal i { width: 4px; border-radius: 1px; background: var(--eic-line); }
.signal i:nth-child(1) { height: 6px; } .signal i:nth-child(2) { height: 11px; } .signal i:nth-child(3) { height: 16px; }
.signal.s1 i:nth-child(1) { background: var(--eic-bad); }
.signal.s2 i:nth-child(-n+2) { background: var(--eic-warn); }
.signal.s3 i { background: var(--eic-ok); }

.grid > .empty { grid-column: 1 / -1; }
.empty { padding: 32px 16px; text-align: center; color: var(--eic-muted); border: 1px dashed var(--eic-line); border-radius: var(--eic-radius); }
.empty h3 { color: var(--eic-text); margin-bottom: 8px; }

.panel-card { padding: 20px; border-radius: var(--eic-radius); background: var(--eic-card); border: 1px solid var(--eic-line); box-shadow: var(--eic-shadow); margin-bottom: 16px; }
.panel-card > header { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.panel-card > header h3 { flex: 1; font-size: 18px; font-weight: 500; }
.panel-card p { line-height: 1.5; }
.gateway-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; align-items: start; }
.gateway-grid .panel-card { margin: 0; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 8px 16px; margin: 0 0 16px; }
.kv dt { color: var(--eic-muted); font-size: 14px; }
.kv dd { margin: 0; overflow-wrap: anywhere; }
.notice { padding: 12px 14px; border-radius: 10px; font-size: 14px; line-height: 1.5; margin: 0 0 12px; background: var(--eic-soft); }
.notice.warn { background: color-mix(in srgb, var(--eic-warn) 14%, transparent); }
.notice.bad { background: color-mix(in srgb, var(--eic-bad) 12%, transparent); }
.history { list-style: none; margin: 0 0 16px; padding: 0; display: grid; gap: 8px; }
.history li { padding: 10px 12px; border: 1px solid var(--eic-line); border-radius: 10px; font-size: 14px; }
.separator { border: 0; border-top: 1px solid var(--eic-line); margin: 16px 0; }

.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
.field > span { font-size: 14px; font-weight: 500; }
.field small { color: var(--eic-muted); font-size: 13px; line-height: 1.4; }
input[type="text"], input[type="search"], select, textarea {
  width: 100%; min-height: 44px; padding: 8px 12px; border: 1px solid var(--eic-line); border-radius: 10px;
  background: var(--eic-card); color: var(--eic-text); font: inherit; font-size: 16px;
}
input[readonly] { background: var(--eic-bg); color: var(--eic-muted); }
input:focus-visible, select:focus-visible, button:focus-visible, a:focus-visible, summary:focus-visible {
  outline: 2px solid var(--eic-accent); outline-offset: 2px;
}
.check { display: inline-flex; align-items: center; gap: 10px; min-height: 44px; font-size: 15px; cursor: pointer; }
.check input { width: 20px; height: 20px; margin: 0; font-size: 16px; accent-color: var(--eic-accent); }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

.tg-table { border: 1px solid var(--eic-line); border-radius: var(--eic-radius); background: var(--eic-card); overflow: hidden; }
.tg-row { display: grid; grid-template-columns: 130px 200px 70px 64px minmax(0, 1fr); gap: 12px; padding: 10px 16px; border-top: 1px solid var(--eic-line); align-items: start; }
.tg-row:first-child { border-top: 0; }
.tg-head { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--eic-muted); background: var(--eic-bg); }
.tg-row [data-label]::before { display: none; }
.tg-cell-content p { margin: 0 0 4px; }
.tg-sender small { display: block; color: var(--eic-muted); font-size: 13px; }
.more { display: flex; justify-content: center; padding: 16px 0; }
.more[hidden] { display: none; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin-bottom: 12px; }
.filters .field { margin: 0; min-width: 200px; }
.filters .sheet-head, .filters .sheet-actions, .filter-toggle { display: none; }
details.panel-card summary { cursor: pointer; font-size: 18px; font-weight: 500; min-height: 44px; display: flex; align-items: center; gap: 8px; }
details.panel-card[open] summary { margin-bottom: 12px; }

.type-card .eeps { display: flex; flex-wrap: wrap; gap: 6px; }
.type-card details summary { cursor: pointer; color: var(--eic-accent); font-weight: 500; min-height: 44px; display: flex; align-items: center; }
.type-card details p { margin: 0; font-size: 14px; line-height: 1.5; }
.entity-list { margin: 0; padding-left: 18px; font-size: 14px; line-height: 1.6; }

.toasts { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); display: grid; gap: 8px; z-index: 30; width: min(560px, calc(100% - 32px)); pointer-events: none; }
.toast {
  display: flex; align-items: flex-start; gap: 12px; padding: 12px 12px 12px 16px; border-radius: 12px;
  background: #2b2f36; color: #fff; font-size: 15px; line-height: 1.45; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
  pointer-events: auto; animation: rise 0.18s ease-out;
}
.toast.error { background: #8f2525; }
.toast.ok { background: #1f6b43; }
.toast p { margin: 0; flex: 1; padding-top: 10px; }
.toast button { flex: none; min-width: 44px; min-height: 44px; border: 0; background: none; color: inherit; font-size: 20px; cursor: pointer; }
@keyframes rise { from { transform: translateY(8px); opacity: 0; } }

#layer { display: none; }
#layer.open { display: block; }
.scrim { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.45); z-index: 40; }
.dialog {
  position: fixed; z-index: 41; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: min(560px, calc(100% - 32px)); max-height: calc(100dvh - 48px); display: flex; flex-direction: column;
  background: var(--eic-card); color: var(--eic-text); border-radius: 18px; box-shadow: 0 12px 48px rgba(0, 0, 0, 0.3);
}
.dialog > header { padding: 20px 24px 8px; }
.dialog > header h2 { margin: 0; font-size: 20px; }
.dialog .body { padding: 8px 24px; overflow-y: auto; }
.dialog .body p { line-height: 1.5; }
.dialog .actions { display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 24px 20px; border-top: 1px solid var(--eic-line); }
.dialog .danger-zone { margin-top: 8px; padding-top: 16px; border-top: 1px dashed var(--eic-line); }
.dialog .danger-zone p { margin: 0 0 8px; font-size: 14px; color: var(--eic-muted); }
.type-info { padding: 12px 14px; border-radius: 10px; background: var(--eic-bg); font-size: 14px; line-height: 1.5; margin-bottom: 14px; }
.type-info p { margin: 0 0 6px; }
.type-info p:last-child { margin: 0; }
footer.foot { padding: 8px 24px 24px; text-align: center; color: var(--eic-muted); font-size: 13px; }

@media (max-width: 767px) {
  header.bar { min-height: 56px; padding: 0 12px 0 16px; }
  .bar .sub { display: none; }
  .bar .name { font-size: 18px; }
  .bar .pills { gap: 6px; flex-wrap: nowrap; }
  .bar .pill { font-size: 12px; padding: 2px 8px; white-space: nowrap; }
  .bar .pill .long { display: none; }
  .bar .pill .short { display: inline; }
  nav.tabs {
    order: 3; padding: 0 0 env(safe-area-inset-bottom); gap: 0; overflow: visible;
    border-bottom: 0; border-top: 1px solid var(--eic-line);
  }
  nav.tabs button {
    flex: 1 1 0; flex-direction: column; gap: 2px; min-height: 60px; padding: 6px 2px;
    border-bottom: 0; border-top: 3px solid transparent; font-size: 12px;
  }
  nav.tabs button[aria-selected="true"] { border-top-color: var(--eic-accent); border-bottom-color: transparent; }
  nav.tabs button .long { display: none; }
  nav.tabs button .short { display: inline; }
  nav.tabs ha-icon { --mdc-icon-size: 24px; }
  section { padding: 16px; }
  h2 { font-size: clamp(20px, 6vw, 24px); }
  .grid { grid-template-columns: minmax(0, 1fr); gap: 12px; }
  .toolbar .btn { flex: 1 1 auto; }
  .search { min-width: 100%; }
  #type-chips { display: none; }
  .field.type-category-select { display: flex; }
  .gateway-grid { grid-template-columns: 1fr; }
  .row2 { grid-template-columns: 1fr; gap: 0; }
  .kv { grid-template-columns: 1fr; gap: 2px; }
  .kv dd { margin-bottom: 8px; }

  .tg-table { border: 0; background: none; display: grid; gap: 12px; }
  .tg-head { display: none; }
  .tg-row {
    grid-template-columns: 1fr auto; gap: 4px 12px; padding: 12px 16px;
    border: 1px solid var(--eic-line); border-radius: var(--eic-radius); background: var(--eic-card);
  }
  .tg-row:first-child { border-top: 1px solid var(--eic-line); }
  .tg-row .tg-time { grid-column: 1; color: var(--eic-muted); font-size: 13px; }
  .tg-row .tg-signal { grid-column: 2; grid-row: 1; justify-self: end; }
  .tg-row .tg-sender { grid-column: 1 / -1; }
  .tg-row .tg-rorg { display: none; }
  .tg-row .tg-cell-content { grid-column: 1 / -1; }

  .filter-toggle { display: inline-flex; }
  .filters {
    display: none; position: fixed; z-index: 41; left: 0; right: 0; bottom: 0;
    flex-direction: column; align-items: stretch; gap: 0; margin: 0; padding: 0 16px calc(16px + env(safe-area-inset-bottom));
    max-height: 85dvh; overflow-y: auto; background: var(--eic-card); border-radius: 18px 18px 0 0;
    box-shadow: 0 -8px 32px rgba(0, 0, 0, 0.25);
  }
  .filters.open { display: flex; }
  .filters .field { margin-bottom: 14px; min-width: 0; }
  .filters .sheet-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; position: sticky; top: 0; background: var(--eic-card); }
  .filters .sheet-actions { display: flex; position: sticky; bottom: calc(-16px - env(safe-area-inset-bottom)); padding: 12px 0; background: var(--eic-card); }
  .filters .sheet-actions .btn { flex: 1; }
  .filters-scrim { position: fixed; inset: 0; z-index: 40; background: rgba(0, 0, 0, 0.45); }

  .dialog {
    left: 0; right: 0; top: auto; bottom: 0; transform: none; width: 100%;
    max-height: 92dvh; border-radius: 18px 18px 0 0; padding-bottom: env(safe-area-inset-bottom);
  }
  .dialog > header { padding: 16px 16px 4px; }
  .dialog .body { padding: 8px 16px; }
  .dialog .actions { padding: 12px 16px 16px; position: sticky; bottom: 0; background: var(--eic-card); }
  .dialog .actions .btn { flex: 1 1 auto; }
  .toasts { bottom: calc(76px + env(safe-area-inset-bottom)); }
}
`;

class EnoceanChipPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "devices";
    this._scroll = {};
    this._data = {
      status: null,
      devices: [],
      heard: [],
      heardMeta: { listening: false, snapshot: false },
      types: [],
      categories: [],
      areas: [],
      telegrams: [],
      telegramTotal: 0,
      gateway: null,
      simulate: null,
    };
    this._loaded = {};
    this._prefs = { tab: "devices", pageSize: 100 };
    this._filters = {
      search: "",
      nearby: "all",
      typeCategory: "",
      typeSearch: "",
      sender: "",
      adoptedOnly: false,
    };
    this._live = false;
    this._notes = {};
    this._signatures = {};
    this._timers = [];
    this._pairingUntil = 0;
  }

  // ── Home Assistant hooks ──────────────────────────────────────────

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._start();
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrowValue = value;
    this.toggleAttribute("narrow", value === true);
  }

  set panel(value) {
    this._panel = value;
  }

  set route(value) {
    this._route = value;
  }

  connectedCallback() {
    if (this._hass && !this._started) this._start();
  }

  disconnectedCallback() {
    this._timers.forEach((timer) => clearInterval(timer));
    this._timers = [];
    this._started = false;
  }

  // ── plumbing ──────────────────────────────────────────────────────

  $(selector) {
    return this.shadowRoot.querySelector(selector);
  }

  _api(method, path, body) {
    return this._hass.callApi(method, `${DOMAIN}/${path}`, body);
  }

  async _busy(button, job) {
    if (button) {
      button.disabled = true;
      button.classList.add("busy");
    }
    try {
      return await job();
    } catch (err) {
      this._toast(errorText(err), "error");
      return undefined;
    } finally {
      if (button && button.isConnected) {
        button.disabled = false;
        button.classList.remove("busy");
      }
    }
  }

  _changed(key, value) {
    const signature = JSON.stringify(value);
    if (this._signatures[key] === signature) return false;
    this._signatures[key] = signature;
    return true;
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }

  _toast(message, kind = "") {
    const box = this.$("#toasts");
    if (!box) return;
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.setAttribute("role", kind === "error" ? "alert" : "status");
    toast.innerHTML = `<p>${esc(message)}</p><button type="button" aria-label="Dismiss">×</button>`;
    toast.querySelector("button").addEventListener("click", () => toast.remove());
    box.append(toast);
    while (box.children.length > 3) box.firstElementChild.remove();
    setTimeout(() => toast.remove(), kind === "error" ? 9000 : 5000);
  }

  async _loadPrefs() {
    try {
      const result = await this._hass.callWS({ type: "frontend/get_user_data", key: PREFS_KEY });
      if (result?.value) this._prefs = { ...this._prefs, ...result.value };
    } catch (err) {
      // Preferences are a convenience: the panel works without them.
    }
    if (!PAGE_SIZES.includes(this._prefs.pageSize)) this._prefs.pageSize = 100;
    if (!TABS.some((tab) => tab.key === this._prefs.tab)) this._prefs.tab = "devices";
  }

  _savePrefs() {
    clearTimeout(this._prefsTimer);
    this._prefsTimer = setTimeout(() => {
      this._hass.callWS({ type: "frontend/set_user_data", key: PREFS_KEY, value: this._prefs }).catch(() => {});
    }, 600);
  }

  // ── start ─────────────────────────────────────────────────────────

  async _start() {
    this._started = true;
    if (!this._rendered) this._renderShell();
    await this._loadPrefs();
    this._showTab(this._prefs.tab, { save: false });
    this._refreshStatus();
    this._timers.push(
      setInterval(() => !document.hidden && this._refreshStatus(), 5000),
      setInterval(() => this._tick(), 1000),
      setInterval(() => {
        if (document.hidden) return;
        if (this._tab === "nearby") this._loadHeard();
        if (this._tab === "telegrams" && this._live) this._loadTelegrams({ live: true });
      }, 3000),
      setInterval(() => !document.hidden && this._tab === "devices" && this._loadDevices(), 15000),
    );
  }

  _renderShell() {
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="app">
        <header class="bar">
          <button class="icon-btn menu" data-action="menu" aria-label="Open the sidebar">${icon("mdi:menu")}</button>
          <div class="title"><span class="name">EnOcean</span><span class="sub">Internal Chip</span></div>
          <div class="pills" id="pills"></div>
        </header>
        <nav class="tabs" role="tablist" aria-label="Sections">
          ${TABS.map(
            (tab) => `<button role="tab" id="tab-${tab.key}" data-tab="${tab.key}" aria-controls="sec-${tab.key}" aria-selected="false">
              ${icon(tab.icon)}<span class="long">${esc(tab.label)}</span><span class="short">${esc(tab.short || tab.label)}</span></button>`,
          ).join("")}
        </nav>
        <main id="main">
          ${this._devicesSkeleton()}
          ${this._nearbySkeleton()}
          ${this._telegramsSkeleton()}
          ${this._typesSkeleton()}
          <section id="sec-gateway" role="tabpanel" aria-labelledby="tab-gateway" hidden>
            <h2>Gateway</h2>
            <div id="gateway-body" class="gateway-grid"><p class="muted">Loading…</p></div>
          </section>
          <footer class="foot" id="foot"></footer>
        </main>
        <div class="toasts" id="toasts" aria-live="polite"></div>
        <div id="layer"></div>
      </div>`;
    const root = this.shadowRoot;
    root.addEventListener("click", (event) => this._onClick(event));
    root.addEventListener("input", (event) => this._onInput(event));
    root.addEventListener("change", (event) => this._onChange(event));
    root.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (this.$("#layer").classList.contains("open")) this._closeDialog();
      else if (this.$("#tg-filters")?.classList.contains("open")) this._closeFilters();
    });
  }

  _devicesSkeleton() {
    return `
      <section id="sec-devices" role="tabpanel" aria-labelledby="tab-devices" hidden>
        <h2>Devices</h2>
        <p class="intro">Devices adopted by this gateway. Each one appears in Home Assistant with its own entities.</p>
        <div class="toolbar">
          <label class="search">${icon("mdi:magnify")}<input type="search" id="device-search" placeholder="Search by name, ID, type or area" aria-label="Search devices"></label>
          <button class="btn primary" data-action="adopt-manual">${icon("mdi:plus")}Adopt by ID</button>
        </div>
        <p class="count" id="devices-count"></p>
        <div class="grid" id="devices-list"></div>
      </section>`;
  }

  _nearbySkeleton() {
    return `
      <section id="sec-nearby" role="tabpanel" aria-labelledby="tab-nearby" hidden>
        <h2>Nearby</h2>
        <p class="intro">The radio is always received. Listening only decides whether a transmitter never heard before joins this list, so the neighbours' sensors stay out of it when you are not looking for something.</p>
        <div class="toolbar" id="nearby-controls"></div>
        <div class="toolbar">
          <div class="chips" role="group" aria-label="Show">
            <button class="chip" data-action="nearby-filter" data-value="all" aria-pressed="true">All</button>
            <button class="chip" data-action="nearby-filter" data-value="free" aria-pressed="false">Not adopted</button>
            <button class="chip" data-action="nearby-filter" data-value="teach" aria-pressed="false">Teach-in only</button>
          </div>
        </div>
        <p class="count" id="nearby-count"></p>
        <div class="grid" id="nearby-list"></div>
      </section>`;
  }

  _telegramsSkeleton() {
    return `
      <section id="sec-telegrams" role="tabpanel" aria-labelledby="tab-telegrams" hidden>
        <h2>Telegrams</h2>
        <p class="intro">Everything the radio received in the last 7 days, newest first, up to 2000 telegrams.</p>
        <div class="filters" id="tg-filters" role="group" aria-label="Filters">
          <div class="sheet-head"><h3>Filters</h3><button class="btn ghost" data-action="close-filters">Close</button></div>
          <label class="field"><span>Sender</span><select id="tg-sender"><option value="">All senders</option></select></label>
          <label class="check"><input type="checkbox" id="tg-adopted"> Only adopted devices</label>
          <label class="field"><span>Rows per page</span><select id="tg-size">${PAGE_SIZES.map((size) => `<option value="${size}">${size}</option>`).join("")}</select></label>
          <div class="sheet-actions"><button class="btn primary" data-action="apply-filters">Apply filters</button></div>
        </div>
        <div class="toolbar">
          <button class="btn filter-toggle" data-action="open-filters">${icon("mdi:filter-variant")}Filters <span id="tg-filter-count"></span></button>
          <div class="chips" id="tg-chips"></div>
          <span class="grow"></span>
          <label class="check"><input type="checkbox" id="tg-live"> Live</label>
          <button class="btn" data-action="tg-refresh">${icon("mdi:refresh")}Refresh</button>
          <button class="btn danger-outline" data-action="tg-clear">${icon("mdi:delete-sweep-outline")}Clear log</button>
        </div>
        <p class="count" id="tg-count"></p>
        <div id="tg-list"></div>
        <div class="more" id="tg-more-box" hidden><button class="btn" data-action="tg-more" id="tg-more">Load more</button></div>
        <details class="panel-card" id="sim-card">
          <summary>${icon("mdi:flask-outline")}Test telegrams</summary>
          <div id="sim-body"><p class="muted">Loading…</p></div>
        </details>
      </section>`;
  }

  _typesSkeleton() {
    return `
      <section id="sec-types" role="tabpanel" aria-labelledby="tab-types" hidden>
        <h2>Device types</h2>
        <p class="intro">What this integration can handle. <strong>Tested on hardware</strong> types have been run against real devices; the others follow the EnOcean Equipment Profile specification and have not been checked on a device yet.</p>
        <div class="toolbar">
          <label class="search">${icon("mdi:magnify")}<input type="search" id="type-search" placeholder="Search by name or profile, like A5-02" aria-label="Search device types"></label>
        </div>
        <div class="chips" id="type-chips" role="group" aria-label="Category" style="margin-bottom:12px"></div>
        <label class="field type-category-select"><span>Category</span><select id="type-category"></select></label>
        <p class="count" id="types-count"></p>
        <div class="grid" id="types-list"></div>
      </section>`;
  }

  // ── tabs ──────────────────────────────────────────────────────────

  _showTab(key, { save = true } = {}) {
    const main = this.$("#main");
    if (this._tab) this._scroll[this._tab] = main.scrollTop;
    this._tab = key;
    for (const tab of TABS) {
      this.$(`#tab-${tab.key}`).setAttribute("aria-selected", String(tab.key === key));
      this.$(`#sec-${tab.key}`).hidden = tab.key !== key;
    }
    main.scrollTop = this._scroll[key] || 0;
    if (save) {
      this._prefs.tab = key;
      this._savePrefs();
    }
    this._refreshTab(key);
  }

  async _refreshTab(key) {
    if (key === "devices") {
      await this._ensureCatalog();
      this._loadDevices();
    } else if (key === "nearby") {
      await this._ensureCatalog();
      this._loadHeard();
    } else if (key === "telegrams") {
      this.$("#tg-size").value = String(this._prefs.pageSize);
      this._loadTelegrams();
      this._loadSenders();
      this._loadSimulation();
    } else if (key === "types") {
      await this._ensureCatalog();
      this._renderTypes();
    } else if (key === "gateway") {
      this._loadGateway();
    }
  }

  async _ensureCatalog(force = false) {
    if (this._loaded.catalog && !force) return;
    try {
      const [types, areas] = await Promise.all([this._api("GET", "types"), this._api("GET", "areas")]);
      this._data.types = types.types;
      this._data.categories = types.categories;
      this._data.areas = areas.areas;
      this._loaded.catalog = true;
    } catch (err) {
      this._toast(errorText(err), "error");
    }
  }

  _type(key) {
    return this._data.types.find((type) => type.key === key);
  }

  _category(key) {
    return this._data.categories.find((category) => category.key === key)?.name || key;
  }

  // ── status and pills ──────────────────────────────────────────────

  async _refreshStatus() {
    try {
      const status = await this._api("GET", "status");
      this._data.status = status;
      this._pairingUntil = status.pairing.open ? Date.now() + status.pairing.seconds * 1000 : 0;
      this._renderPills();
      const foot = this.$("#foot");
      if (foot) foot.textContent = `EnOcean Internal Chip ${status.version}`;
    } catch (err) {
      this._renderPills(errorText(err));
    }
  }

  _renderPills(error) {
    const box = this.$("#pills");
    if (!box) return;
    const status = this._data.status;
    let html;
    if (error || !status) {
      html = pill("Not loaded", "bad");
    } else {
      const gateway = status.gateway;
      const twoWay = (long, short) => `<span class="long">${long}</span><span class="short">${short}</span>`;
      const state = gateway.ready
        ? ["ok", twoWay("Gateway ready", "Ready")]
        : gateway.connected
          ? ["warn", twoWay("Reading address", "Reading")]
          : ["bad", twoWay("Gateway offline", "Offline")];
      html = `<span class="pill ${state[0]}">${state[1]}</span>`;
      if (status.listening) html += `<span class="pill warn">${twoWay("Listening", "Listening")}</span>`;
      if (this._pairingUntil > Date.now()) html += `<span class="pill warn" id="pairing-pill">${twoWay("Pairing", "Pair")} <span data-countdown></span></span>`;
    }
    if (this._changed("pills", html)) box.innerHTML = html;
    this._tick();
  }

  _tick() {
    const left = Math.max(0, Math.round((this._pairingUntil - Date.now()) / 1000));
    const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    this.shadowRoot.querySelectorAll("[data-countdown]").forEach((node) => {
      node.textContent = text;
    });
    if (this._pairingUntil && left === 0) {
      this._pairingUntil = 0;
      this._renderPills();
      if (this._tab === "gateway") this._loadGateway();
    }
  }

  // ── events ────────────────────────────────────────────────────────

  _onClick(event) {
    const dialogTarget = event.target.closest("[data-dlg]");
    if (dialogTarget && this._dialogHandlers) {
      const name = dialogTarget.dataset.dlg;
      if (name === "close") return this._closeDialog();
      const handler = this._dialogHandlers[name];
      if (handler) handler(dialogTarget);
      return undefined;
    }
    const tab = event.target.closest("[data-tab]");
    if (tab) return this._showTab(tab.dataset.tab);
    const target = event.target.closest("[data-action]");
    if (!target) return undefined;
    const { action, id, value } = target.dataset;
    const handlers = {
      menu: () => this.dispatchEvent(new CustomEvent("hass-toggle-menu", { bubbles: true, composed: true })),
      "adopt-manual": () => this._adoptDialog(null),
      adopt: () => this._adoptDialog(this._data.heard.find((item) => item.id === id)),
      pair: () => this._pair(target, id),
      edit: () => this._editDialog(id),
      "open-device": () => this._navigate(`/config/devices/device/${value}`),
      "goto-device": () => {
        this._filters.search = id;
        this.$("#device-search").value = id;
        this._showTab("devices");
      },
      mute: () => this._mute(target, id),
      forget: () => this._forgetDialog(id),
      "nearby-filter": () => this._setNearbyFilter(value),
      listen: () => this._listen(target, value),
      "nearby-clear": () => this._clearNearbyDialog(),
      "nearby-snapshot": () => this._snapshotDialog(),
      "type-category": () => {
        this._filters.typeCategory = value;
        this._renderTypes();
      },
      "tg-refresh": () => this._busy(target, () => this._loadTelegrams({ force: true })),
      "tg-more": () => this._busy(target, () => this._loadTelegrams({ append: true })),
      "tg-clear": () => this._clearTelegramsDialog(),
      "open-filters": () => this._openFilters(),
      "close-filters": () => this._closeFilters(),
      "apply-filters": () => {
        this._readFilters();
        this._closeFilters();
      },
      "remove-filter": () => this._removeFilter(value),
      "sim-send": () => this._simulate(target),
      "sim-custom": () => this._simulateCustom(target),
      "open-options": () => this._navigate(`/config/integrations/integration/${DOMAIN}`),
      "gateway-refresh": () => this._gatewayAddress(target, { action: "refresh" }, "Address read again."),
      reload: () => this._reload(target),
      "pairing-open": () => this._pairingWindow(target, "open"),
      "pairing-close": () => this._pairingWindow(target, "close"),
      unlock: () => this._unlockDialog(),
      lock: () => this._gatewayAddress(target, { action: "lock" }, "Address changes are locked again."),
      "address-write": () => this._addressDialog("write"),
      "address-restore": () => this._addressDialog("restore"),
    };
    handlers[action]?.();
    return undefined;
  }

  _onInput(event) {
    const target = event.target;
    if (target.id === "device-search") {
      this._filters.search = target.value;
      this._renderDevices();
    } else if (target.id === "type-search") {
      this._filters.typeSearch = target.value;
      this._renderTypes();
    }
  }

  _onChange(event) {
    const target = event.target;
    if (this._dialogHandlers && target.closest(".dialog")) {
      this._dialogHandlers[`change:${target.name}`]?.(target);
      return;
    }
    if (target.id === "tg-live") {
      this._live = target.checked;
      if (this._live) this._loadTelegrams({ live: true });
    } else if (["tg-sender", "tg-adopted", "tg-size"].includes(target.id) && !PHONE.matches) {
      this._readFilters();
    } else if (target.id === "type-category") {
      this._filters.typeCategory = target.value;
      this._renderTypes();
    } else if (target.id === "sim-preset") {
      const preset = this._data.simulate?.presets.find((item) => item.key === target.value);
      if (preset) this.$("#sim-sender").value = preset.default_sender;
    } else if (target.id === "sim-device" || target.id === "sim-custom-device") {
      const input = this.$(target.id === "sim-device" ? "#sim-sender" : "#sim-custom-sender");
      if (target.value) input.value = target.value;
    }
  }

  // ── devices ───────────────────────────────────────────────────────

  async _loadDevices() {
    try {
      const result = await this._api("GET", "devices");
      this._data.devices = result.devices;
      this._renderDevices();
    } catch (err) {
      this._toast(errorText(err), "error");
    }
  }

  _renderDevices() {
    const list = this.$("#devices-list");
    if (!list) return;
    const query = this._filters.search.trim().toLowerCase();
    const devices = this._data.devices.filter((device) =>
      !query ||
      [device.display_name, device.id, compactId(device.id), device.type_name, device.area_name, device.eep]
        .filter(Boolean)
        .some((text) => String(text).toLowerCase().includes(query)),
    );
    const total = this._data.devices.length;
    this.$("#devices-count").textContent = query
      ? `${devices.length} of ${plural(total, "device")}`
      : plural(total, "device");
    let html;
    if (!total) {
      html = `<div class="empty"><h3>No devices yet</h3><p>Open <strong>Nearby</strong>, listen for new devices and adopt them from there. Devices that never transmit, like one-way actuators, can be adopted by ID.</p></div>`;
    } else if (!devices.length) {
      html = `<div class="empty"><h3>Nothing matches “${esc(this._filters.search)}”</h3></div>`;
    } else {
      html = devices.map((device) => this._deviceCard(device)).join("");
    }
    if (this._changed("devices", html)) list.innerHTML = html;
  }

  _deviceCard(device) {
    const type = this._type(device.type);
    const color = CATEGORY_COLORS[type?.category] || "";
    const note = this._notes[device.id];
    const actuator = ["ute", "rps", "4bs"].includes(device.pairing);
    const pills = [
      device.verified ? pill("Tested on hardware", "ok", "mdi:check-decagram") : pill("From the specification", "", "mdi:file-document-outline"),
      device.keeps_asking ? pill("Keeps asking to pair", "warn", "mdi:alert-outline") : "",
      actuator && !device.can_send ? pill("Gateway not ready", "bad") : "",
      device.sender ? pill(`Sender ${device.sender}`, "", "mdi:send-outline") : "",
    ].join("");
    return `
      <article class="card accent" style="${color ? `--cat:${color}` : ""}" data-device="${esc(device.id)}">
        <div class="card-head">
          <span class="avatar">${icon(type?.icon || "mdi:radio-tower")}</span>
          <div class="head-text">
            <h3 title="${esc(device.display_name)}">${esc(device.display_name)}</h3>
            <p class="muted">${esc(device.type_name || "Unknown type")} · <span class="mono">${esc(device.eep)}</span></p>
          </div>
          ${signal(device.dbm)}
        </div>
        <dl class="facts">
          <div><dt>Area</dt><dd>${esc(device.area_name || "No area")}</dd></div>
          <div><dt>Last heard</dt><dd>${esc(ago(device.last_seen))}</dd></div>
          <div><dt>ID</dt><dd class="mono">${esc(device.id)}</dd></div>
          <div><dt>Entities</dt><dd>${device.entities.length}</dd></div>
        </dl>
        <div class="pills-row">${pills}</div>
        <p class="card-note ${note?.kind || ""}">${esc(note?.text || "")}</p>
        <div class="card-actions">
          ${device.pairing && device.pairing !== "none" ? `<button class="btn primary" data-action="pair" data-id="${esc(device.id)}">${icon("mdi:link-variant")}Pair</button>` : ""}
          <button class="btn" data-action="edit" data-id="${esc(device.id)}">${icon("mdi:pencil-outline")}Edit</button>
          ${device.ha_device_id ? `<button class="btn ghost" data-action="open-device" data-value="${esc(device.ha_device_id)}">Open in Home Assistant</button>` : ""}
        </div>
      </article>`;
  }

  async _pair(button, id) {
    const result = await this._busy(button, () => this._api("POST", `devices/${compactId(id)}/pair`));
    if (!result) return;
    this._notes[id] = { text: result.message, kind: result.result === "keeps_asking" ? "bad" : "" };
    if (result.pairing?.open) this._pairingUntil = Date.now() + result.pairing.seconds * 1000;
    this._renderDevices();
    this._renderPills();
    this._toast(result.message, result.result === "keeps_asking" ? "error" : "ok");
  }

  // ── adopt and edit dialogs ────────────────────────────────────────

  _typeOptions(selected, suggested) {
    const types = this._data.types;
    const used = types.filter((type) => type.usage > 0).sort((a, b) => b.usage - a.usage).slice(0, 5);
    const option = (type) =>
      `<option value="${esc(type.key)}" ${type.key === selected ? "selected" : ""}>${esc(type.name)}${type.key === suggested ? " (suggested)" : ""}</option>`;
    let html = "";
    if (used.length) html += `<optgroup label="Most used">${used.map(option).join("")}</optgroup>`;
    for (const category of this._data.categories) {
      const inCategory = types.filter((type) => type.category === category.key && !used.includes(type));
      if (inCategory.length) html += `<optgroup label="${esc(category.name)}">${inCategory.map(option).join("")}</optgroup>`;
    }
    return html;
  }

  _eepOptions(type, selected) {
    return (type?.eeps || []).map((eep) => `<option value="${eep}" ${eep === selected ? "selected" : ""}>${eep}</option>`).join("");
  }

  _areaOptions(selected) {
    return `<option value="">No area</option>${this._data.areas
      .map((area) => `<option value="${esc(area.id)}" ${area.id === selected ? "selected" : ""}>${esc(area.name)}</option>`)
      .join("")}`;
  }

  _typeInfo(type) {
    if (!type) return "";
    return `
      <p>${esc(type.description)}</p>
      <p><strong>${esc(PAIRING_TEXT[type.pairing] || type.pairing)}</strong> · ${type.verified ? "tested on hardware" : "from the specification"}</p>
      ${type.notes ? `<p>${esc(type.notes)}</p>` : ""}`;
  }

  _deviceFields({ id, idReadonly, name, typeKey, suggested, eep, areaId, manufacturer }) {
    const type = this._type(typeKey) || this._data.types[0];
    return `
      <label class="field"><span>Device ID</span>
        <input type="text" name="id" value="${esc(id || "")}" ${idReadonly ? "readonly" : ""} placeholder="01:A2:B3:C4" autocomplete="off" spellcheck="false">
        ${idReadonly ? "" : `<small>Printed on the device or its box: 4 bytes in hexadecimal.</small>`}
      </label>
      <label class="field"><span>Name</span><input type="text" name="name" value="${esc(name || "")}" maxlength="80" placeholder="Kitchen window" autocomplete="off"></label>
      <label class="field"><span>Area</span><select name="area">${this._areaOptions(areaId)}</select></label>
      <label class="field"><span>Device type</span><select name="type">${this._typeOptions(type?.key, suggested)}</select></label>
      <div class="type-info" id="type-info">${this._typeInfo(type)}</div>
      <div class="row2">
        <label class="field"><span>Profile (EEP)</span><select name="eep">${this._eepOptions(type, eep || type?.eeps[0])}</select></label>
        <label class="field" id="manufacturer-field" ${type?.pairing === "4bs" ? "" : "hidden"}><span>Manufacturer ID</span>
          <input type="text" name="manufacturer" value="${esc(manufacturer || "7FF")}" maxlength="3" spellcheck="false" autocomplete="off">
          <small>Sent in the teach-in telegram. 7FF works with most actuators.</small>
        </label>
      </div>`;
  }

  _typeChanged(select, eepWanted) {
    const dialog = this.$(".dialog");
    const type = this._type(select.value);
    dialog.querySelector("#type-info").innerHTML = this._typeInfo(type);
    const eepSelect = dialog.querySelector('select[name="eep"]');
    const keep = type?.eeps.includes(eepWanted) ? eepWanted : type?.eeps[0];
    eepSelect.innerHTML = this._eepOptions(type, keep);
    dialog.querySelector("#manufacturer-field").hidden = type?.pairing !== "4bs";
  }

  _readDeviceForm(dialog) {
    const value = (name) => dialog.querySelector(`[name="${name}"]`)?.value.trim() ?? "";
    const type = this._type(value("type"));
    const body = { name: value("name"), type: value("type"), eep: value("eep"), area_id: value("area") || null };
    if (type?.pairing === "4bs") {
      const manufacturer = parseInt(value("manufacturer") || "7FF", 16);
      if (Number.isNaN(manufacturer) || manufacturer < 0 || manufacturer > 0x7ff) throw new Error("The manufacturer ID is 3 hexadecimal digits, from 000 to 7FF");
      body.manufacturer_id = manufacturer;
    }
    return body;
  }

  _adoptDialog(heard) {
    const suggested = heard?.suggested_type || null;
    const typeKey = suggested || this._data.types.find((type) => type.usage > 0)?.key || this._data.types[0]?.key;
    this._openDialog(
      `<header><h2 id="dlg-title">${heard ? "Adopt this device" : "Adopt a device by ID"}</h2></header>
       <div class="body">
         ${heard?.eep ? `<p class="notice">It declared profile <strong>${esc(heard.eep)}</strong> in its teach-in telegram.</p>` : ""}
         ${heard && !heard.eep ? `<p class="notice">It did not declare a profile: pick the type and profile from the device manual.</p>` : ""}
         ${this._deviceFields({ id: heard?.id, idReadonly: Boolean(heard), name: "", typeKey, suggested, eep: heard?.eep })}
       </div>
       <div class="actions">
         <button class="btn primary" data-dlg="save">${icon("mdi:plus")}Adopt device</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        "change:type": (select) => this._typeChanged(select, heard?.eep),
        save: async (button) => {
          const dialog = this.$(".dialog");
          await this._busy(button, async () => {
            const body = { ...this._readDeviceForm(dialog), id: dialog.querySelector('[name="id"]').value.trim() };
            if (!body.name) throw new Error("Give the device a name");
            const result = await this._api("POST", "devices", body);
            this._closeDialog();
            const type = this._type(body.type);
            const next = type?.pairing === "ute"
              ? " Next: press Pair on its card, then put the device into learn mode."
              : type?.pairing === "rps" || type?.pairing === "4bs"
                ? " Next: put the actuator into learn mode, then press Pair on its card."
                : "";
            this._toast(`${result.device.display_name} adopted.${next}`, "ok");
            this._loaded.catalog = false;
            await this._ensureCatalog();
            this._loadDevices();
            this._loadHeard();
          });
        },
      },
    );
  }

  _editDialog(id) {
    const device = this._data.devices.find((item) => item.id === id);
    if (!device) return;
    const entities = device.entities.length
      ? `<ul class="entity-list">${device.entities.map((entity) => `<li><span class="mono">${esc(entity.entity_id)}</span>${entity.disabled ? " (disabled)" : ""}</li>`).join("")}</ul>`
      : `<p class="muted">No entities yet.</p>`;
    this._openDialog(
      `<header><h2 id="dlg-title">Edit ${esc(device.display_name)}</h2></header>
       <div class="body">
         ${this._deviceFields({
           id: device.id,
           idReadonly: true,
           name: device.display_name,
           typeKey: device.type,
           eep: device.eep,
           areaId: device.area_id,
           manufacturer: device.manufacturer_id != null ? device.manufacturer_id.toString(16).toUpperCase() : "",
         })}
         <p class="notice warn" id="type-warning" hidden>A new type or profile replaces the entities that no longer apply. Entities that stay keep their names and settings.</p>
         <div class="field"><span>Entities</span>${entities}</div>
         <div class="danger-zone">
           <p>Removing the device also removes its ${plural(device.entities.length, "entity", "entities")} from Home Assistant.</p>
           <button class="btn danger-outline" data-dlg="remove">${icon("mdi:delete-outline")}Remove device…</button>
         </div>
       </div>
       <div class="actions">
         <button class="btn primary" data-dlg="save">Save changes</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        "change:type": (select) => {
          this._typeChanged(select, device.eep);
          this._showTypeWarning(device);
        },
        "change:eep": () => this._showTypeWarning(device),
        save: async (button) => {
          const dialog = this.$(".dialog");
          await this._busy(button, async () => {
            const body = this._readDeviceForm(dialog);
            if (!body.name) throw new Error("The name cannot be empty");
            const result = await this._api("PATCH", `devices/${compactId(device.id)}`, body);
            this._closeDialog();
            this._toast(`${result.device.display_name} saved.`, "ok");
            this._loaded.catalog = false;
            await this._ensureCatalog();
            this._loadDevices();
            // A new type reloads the integration: the entity list follows shortly.
            setTimeout(() => this._loadDevices(), 4000);
          });
        },
        remove: () => this._removeDialog(device),
      },
    );
  }

  _showTypeWarning(device) {
    const dialog = this.$(".dialog");
    const type = dialog.querySelector('select[name="type"]').value;
    const eep = dialog.querySelector('select[name="eep"]').value;
    dialog.querySelector("#type-warning").hidden = type === device.type && eep === device.eep;
  }

  _removeDialog(device) {
    const count = device.entities.length;
    const list = count
      ? `<ul class="entity-list">${device.entities.map((entity) => `<li class="mono">${esc(entity.entity_id)}</li>`).join("")}</ul>`
      : "";
    this._openDialog(
      `<header><h2 id="dlg-title">Remove “${esc(device.display_name)}”?</h2></header>
       <div class="body">
         <p>The device leaves this gateway and Home Assistant. ${count ? `Its ${plural(count, "entity", "entities")} will be removed too, with their history links in dashboards and automations:` : "It has no entities."}</p>
         ${list}
         <p class="muted">The device itself keeps its pairing. To use it again, adopt it again: if it is a one-way actuator it will also need a new teach-in.</p>
       </div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">Remove device${count ? ` and ${plural(count, "entity", "entities")}` : ""}</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          await this._busy(button, async () => {
            await this._api("DELETE", `devices/${compactId(device.id)}?entities=remove`);
            this._closeDialog();
            delete this._notes[device.id];
            this._toast(`${device.display_name} removed.`, "ok");
            this._loadDevices();
          });
        },
      },
    );
  }

  _openDialog(html, handlers) {
    const layer = this.$("#layer");
    this._lastFocus = this.shadowRoot.activeElement;
    layer.innerHTML = `<div class="scrim" data-dlg="close"></div><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dlg-title">${html}</div>`;
    layer.classList.add("open");
    this._dialogHandlers = handlers;
    const first = layer.querySelector(".body input:not([readonly]), .body select, .actions .btn");
    first?.focus({ preventScroll: true });
  }

  _closeDialog() {
    const layer = this.$("#layer");
    layer.classList.remove("open");
    layer.innerHTML = "";
    this._dialogHandlers = null;
    if (this._lastFocus?.isConnected) this._lastFocus.focus({ preventScroll: true });
  }

  // ── nearby ────────────────────────────────────────────────────────

  async _loadHeard() {
    try {
      const result = await this._api("GET", "heard");
      this._data.heard = result.devices;
      this._data.heardMeta = { listening: result.listening, snapshot: result.snapshot };
      this._renderNearby();
    } catch (err) {
      if (this._tab === "nearby") this._toast(errorText(err), "error");
    }
  }

  _renderNearby() {
    const { listening, snapshot } = this._data.heardMeta;
    const controls = `
      <button class="btn ${listening ? "" : "primary"}" data-action="listen" data-value="${listening ? "stop" : "start"}">
        ${icon(listening ? "mdi:stop-circle-outline" : "mdi:radar")}${listening ? "Stop listening" : "Listen for new devices"}</button>
      ${snapshot
        ? `<button class="btn" data-action="listen" data-value="drop_snapshot">${icon("mdi:eye-outline")}Show all as known</button>`
        : `<button class="btn" data-action="nearby-snapshot">${icon("mdi:new-box")}Only new from now on</button>`}
      <span class="grow"></span>
      <button class="btn danger-outline" data-action="nearby-clear">${icon("mdi:playlist-remove")}Clear list</button>`;
    const controlsBox = this.$("#nearby-controls");
    if (this._changed("nearby-controls", controls)) controlsBox.innerHTML = controls;

    const filter = this._filters.nearby;
    const items = this._data.heard
      .filter((item) => filter === "all" || (filter === "free" ? !item.adopted : Boolean(item.teach_in)))
      .sort((a, b) =>
        (b.new_since_snapshot ? 1 : 0) - (a.new_since_snapshot ? 1 : 0) ||
        (b.teach_in && !b.adopted ? 1 : 0) - (a.teach_in && !a.adopted ? 1 : 0) ||
        String(b.last_seen || "").localeCompare(String(a.last_seen || "")),
      );
    const free = this._data.heard.filter((item) => !item.adopted).length;
    this.$("#nearby-count").textContent = `${plural(this._data.heard.length, "transmitter")}, ${free} not adopted${snapshot ? " · new ones are highlighted" : ""}`;
    let html;
    if (!items.length) {
      html = listening
        ? `<div class="empty"><h3>Listening…</h3><p>Press a button on the device, open and close a window, or put the device into learn mode.</p></div>`
        : `<div class="empty"><h3>Nothing here yet</h3><p>Press <strong>Listen for new devices</strong>, then make the device transmit.</p></div>`;
    } else {
      html = items.map((item) => this._heardCard(item)).join("");
    }
    const list = this.$("#nearby-list");
    if (this._changed("nearby", html)) list.innerHTML = html;
  }

  _heardCard(item) {
    const type = this._type(item.suggested_type);
    const color = CATEGORY_COLORS[type?.category] || "";
    const title = item.adopted ? item.name : item.suggested_type_name || `${item.rorg || "Unknown"} transmitter`;
    const pills = [
      item.new_since_snapshot ? pill("New", "accent", "mdi:new-box") : "",
      item.teach_in ? pill(`Teach-in${item.eep ? ` · ${item.eep}` : ""}`, "warn", "mdi:school-outline") : "",
      item.adopted ? pill("Adopted", "ok", "mdi:check") : "",
      item.muted ? pill("Muted", "", "mdi:bell-off-outline") : "",
      item.keeps_asking ? pill(`Asked to pair ${item.pairing_requests} times`, "bad", "mdi:alert-outline") : "",
    ].join("");
    return `
      <article class="card accent ${item.new_since_snapshot ? "is-new" : ""}" style="${color ? `--cat:${color}` : ""}">
        <div class="card-head">
          <span class="avatar">${icon(type?.icon || "mdi:access-point")}</span>
          <div class="head-text"><h3 class="mono">${esc(item.id)}</h3><p class="muted">${esc(title)}</p></div>
          ${signal(item.dbm)}
        </div>
        <div class="pills-row">${pills}</div>
        <p class="summary">${esc(item.summary || "No readable data yet")}</p>
        <dl class="facts">
          <div><dt>Heard</dt><dd>${plural(item.count || 0, "time")}</dd></div>
          <div><dt>Last</dt><dd>${esc(ago(item.last_seen))}</dd></div>
        </dl>
        <div class="card-actions">
          ${item.adopted
            ? `<button class="btn" data-action="goto-device" data-id="${esc(item.id)}">Show device</button>`
            : `<button class="btn primary" data-action="adopt" data-id="${esc(item.id)}">${icon("mdi:plus")}Adopt</button>`}
          <button class="btn" data-action="mute" data-id="${esc(item.id)}" title="${item.muted ? "Notify again about teach-ins from it" : "No more notifications about teach-ins from it"}">
            ${icon(item.muted ? "mdi:bell-outline" : "mdi:bell-off-outline")}${item.muted ? "Unmute" : "Mute"}</button>
          ${item.adopted ? "" : `<button class="btn ghost" data-action="forget" data-id="${esc(item.id)}">Forget</button>`}
        </div>
      </article>`;
  }

  _setNearbyFilter(value) {
    this._filters.nearby = value;
    this.shadowRoot.querySelectorAll('[data-action="nearby-filter"]').forEach((chip) => {
      chip.setAttribute("aria-pressed", String(chip.dataset.value === value));
    });
    this._renderNearby();
  }

  async _listen(button, action) {
    const result = await this._busy(button, () => this._api("POST", `listen/${action}`));
    if (!result) return;
    this._data.heardMeta = { listening: result.listening, snapshot: result.snapshot };
    await this._loadHeard();
    this._refreshStatus();
  }

  async _mute(button, id) {
    const result = await this._busy(button, () => this._api("POST", `heard/${compactId(id)}/mute`));
    if (!result) return;
    const item = this._data.heard.find((entry) => entry.id === id);
    if (item) item.muted = result.muted;
    this._renderNearby();
    this._toast(result.muted ? `${id} muted: no more notifications about it.` : `${id} unmuted.`);
  }

  _forgetDialog(id) {
    this._openDialog(
      `<header><h2 id="dlg-title">Forget ${esc(id)}?</h2></header>
       <div class="body"><p>It leaves this list. If it transmits again while listening is on, it comes back. Nothing else changes.</p></div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">Forget it</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          await this._busy(button, async () => {
            await this._api("POST", `heard/${compactId(id)}/forget`);
            this._closeDialog();
            this._data.heard = this._data.heard.filter((item) => item.id !== id);
            this._renderNearby();
          });
        },
      },
    );
  }

  _clearNearbyDialog() {
    const free = this._data.heard.filter((item) => !item.adopted).length;
    if (!free) {
      this._toast("Only adopted devices are in the list: there is nothing to clear.");
      return;
    }
    this._openDialog(
      `<header><h2 id="dlg-title">Clear the list?</h2></header>
       <div class="body"><p>${plural(free, "transmitter")} that ${free === 1 ? "is" : "are"} not adopted will leave the list. Adopted devices stay, and the telegram log is not touched.</p></div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">Clear ${plural(free, "transmitter")}</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          await this._busy(button, async () => {
            await this._api("POST", "listen/clear");
            this._closeDialog();
            await this._loadHeard();
          });
        },
      },
    );
  }

  _snapshotDialog() {
    const free = this._data.heard.filter((item) => !item.adopted).length;
    this._openDialog(
      `<header><h2 id="dlg-title">Only new from now on</h2></header>
       <div class="body">
         <p>Listening starts, and anything heard from now on shows up as new. Then press the button of the device you are looking for: it will be the only new one.</p>
         ${free ? `<p>${plural(free, "transmitter")} that ${free === 1 ? "is" : "are"} not adopted will leave the list first.</p>` : ""}
       </div>
       <div class="actions">
         <button class="btn primary" data-dlg="confirm">Start</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          await this._busy(button, async () => {
            await this._api("POST", "listen/snapshot");
            this._closeDialog();
            await this._loadHeard();
            this._refreshStatus();
          });
        },
      },
    );
  }

  // ── telegrams ─────────────────────────────────────────────────────

  _telegramQuery(offset, limit) {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (this._filters.sender) params.set("sender", compactId(this._filters.sender));
    if (this._filters.adoptedOnly) params.set("adopted", "1");
    return `telegrams?${params}`;
  }

  async _loadTelegrams({ append = false, live = false, force = false } = {}) {
    const size = this._prefs.pageSize;
    try {
      if (append) {
        const result = await this._api("GET", this._telegramQuery(this._data.telegrams.length, size));
        this._data.telegramTotal = result.total;
        this._data.telegrams = this._data.telegrams.concat(result.telegrams);
        this._appendTelegramRows(result.telegrams);
      } else {
        const limit = live ? Math.min(Math.max(this._data.telegrams.length, size), 500) : size;
        const result = await this._api("GET", this._telegramQuery(0, limit));
        this._data.telegramTotal = result.total;
        this._data.telegrams = result.telegrams;
        if (force) this._signatures.telegrams = null;
        this._renderTelegrams();
      }
      this._renderTelegramCount();
    } catch (err) {
      if (!live) this._toast(errorText(err), "error");
    }
  }

  _telegramRow(item) {
    const device = this._data.devices.find((entry) => entry.id === item.sender);
    const badges = [
      item.teach_in ? pill(`Teach-in ${item.teach_in}`, "warn") : "",
      item.simulated ? pill("Simulated", "accent") : "",
    ].join(" ");
    return `
      <div class="tg-row" role="row">
        <span class="tg-time" role="cell">${esc(clock(item.at))}</span>
        <span class="tg-sender" role="cell"><span class="mono">${esc(item.sender)}</span>${device ? `<small>${esc(device.display_name)}</small>` : item.adopted ? "<small>Adopted</small>" : ""}</span>
        <span class="tg-rorg" role="cell">${esc(item.rorg)}</span>
        <span class="tg-signal" role="cell">${signal(item.dbm)}</span>
        <span class="tg-cell-content" role="cell">
          <p>${esc(item.summary || "Not decoded")} ${badges}</p>
          <span class="mono muted">${esc(item.raw)}</span>
        </span>
      </div>`;
  }

  _renderTelegrams() {
    const list = this.$("#tg-list");
    if (!list) return;
    const items = this._data.telegrams;
    let html;
    if (!items.length) {
      html = `<div class="empty"><h3>No telegrams</h3><p>${this._filters.sender || this._filters.adoptedOnly ? "Nothing matches the filters." : "Nothing received yet."}</p></div>`;
    } else {
      html = `<div class="tg-table" role="table" aria-label="Received telegrams">
        <div class="tg-row tg-head" role="row"><span role="columnheader">Time</span><span role="columnheader">Sender</span><span role="columnheader">Type</span><span role="columnheader">Signal</span><span role="columnheader">Content</span></div>
        ${items.map((item) => this._telegramRow(item)).join("")}
      </div>`;
    }
    if (this._changed("telegrams", html)) list.innerHTML = html;
    this._renderFilterChips();
  }

  _appendTelegramRows(items) {
    const table = this.$("#tg-list .tg-table");
    if (!table) return this._renderTelegrams();
    table.insertAdjacentHTML("beforeend", items.map((item) => this._telegramRow(item)).join(""));
    this._signatures.telegrams = null;
    return undefined;
  }

  _renderTelegramCount() {
    const shown = this._data.telegrams.length;
    const total = this._data.telegramTotal;
    this.$("#tg-count").textContent = total ? `1–${shown} of ${total}` : "";
    this.$("#tg-more-box").hidden = shown >= total;
  }

  async _loadSenders() {
    if (!this._data.devices.length) {
      try {
        this._data.devices = (await this._api("GET", "devices")).devices;
      } catch (err) {
        return;
      }
    }
    const select = this.$("#tg-sender");
    const options = this._data.devices
      .map((device) => `<option value="${esc(device.id)}">${esc(device.display_name)} · ${esc(device.id)}</option>`)
      .join("");
    const html = `<option value="">All senders</option>${options}`;
    if (this._changed("senders", html)) {
      select.innerHTML = html;
      select.value = this._filters.sender;
    }
  }

  _readFilters() {
    this._filters.sender = this.$("#tg-sender").value;
    this._filters.adoptedOnly = this.$("#tg-adopted").checked;
    const size = Number(this.$("#tg-size").value);
    if (PAGE_SIZES.includes(size) && size !== this._prefs.pageSize) {
      this._prefs.pageSize = size;
      this._savePrefs();
    }
    this._loadTelegrams({ force: true });
  }

  _renderFilterChips() {
    const chips = [];
    if (this._filters.sender) {
      const device = this._data.devices.find((entry) => entry.id === this._filters.sender);
      chips.push(["sender", `Sender: ${device ? device.display_name : this._filters.sender}`]);
    }
    if (this._filters.adoptedOnly) chips.push(["adopted", "Only adopted devices"]);
    const html = chips
      .map(([key, text]) => `<button class="chip" aria-pressed="true" data-action="remove-filter" data-value="${key}" aria-label="Remove filter ${esc(text)}">${esc(text)}<span class="x" aria-hidden="true">×</span></button>`)
      .join("");
    if (this._changed("tg-chips", html)) this.$("#tg-chips").innerHTML = html;
    this.$("#tg-filter-count").textContent = chips.length ? `(${chips.length})` : "";
  }

  _removeFilter(key) {
    if (key === "sender") {
      this._filters.sender = "";
      this.$("#tg-sender").value = "";
    } else if (key === "adopted") {
      this._filters.adoptedOnly = false;
      this.$("#tg-adopted").checked = false;
    }
    this._loadTelegrams({ force: true });
  }

  _openFilters() {
    const sheet = this.$("#tg-filters");
    this.$("#tg-sender").value = this._filters.sender;
    this.$("#tg-adopted").checked = this._filters.adoptedOnly;
    this.$("#tg-size").value = String(this._prefs.pageSize);
    sheet.classList.add("open");
    const scrim = document.createElement("div");
    scrim.className = "filters-scrim";
    scrim.addEventListener("click", () => this._closeFilters());
    sheet.before(scrim);
    this.$("#tg-sender").focus({ preventScroll: true });
  }

  _closeFilters() {
    this.$("#tg-filters").classList.remove("open");
    this.shadowRoot.querySelectorAll(".filters-scrim").forEach((node) => node.remove());
  }

  _clearTelegramsDialog() {
    const total = this._data.status?.counts?.telegrams ?? this._data.telegramTotal;
    this._openDialog(
      `<header><h2 id="dlg-title">Clear the telegram log?</h2></header>
       <div class="body"><p>All ${plural(total, "telegram")} in the log will be deleted. Devices, entities and their history in Home Assistant are not affected.</p></div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">Delete ${plural(total, "telegram")}</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          await this._busy(button, async () => {
            const result = await this._api("DELETE", "telegrams");
            this._closeDialog();
            this._toast(`${plural(result.cleared, "telegram")} deleted.`, "ok");
            await this._loadTelegrams({ force: true });
            this._refreshStatus();
          });
        },
      },
    );
  }

  // ── simulation ────────────────────────────────────────────────────

  async _loadSimulation() {
    try {
      this._data.simulate = await this._api("GET", "simulate");
    } catch (err) {
      return;
    }
    const sim = this._data.simulate;
    const body = this.$("#sim-body");
    if (!sim.allowed) {
      const html = `<p>Simulated telegrams are off. Turn on <strong>Allow simulated telegrams</strong> in the integration options to try devices without hardware.</p>
        <button class="btn" data-action="open-options">${icon("mdi:cog-outline")}Open the integration</button>`;
      if (this._changed("sim", html)) body.innerHTML = html;
      return;
    }
    const groups = [...new Set(sim.presets.map((preset) => preset.group))];
    const presets = groups
      .map((group) => `<optgroup label="${esc(group)}">${sim.presets
        .filter((preset) => preset.group === group)
        .map((preset) => `<option value="${esc(preset.key)}">${esc(preset.name)} · ${esc(preset.eep)}</option>`)
        .join("")}</optgroup>`)
      .join("");
    const deviceOptions = `<option value="">Choose an adopted device…</option>${this._data.devices
      .map((device) => `<option value="${esc(device.id)}">${esc(device.display_name)} · ${esc(device.id)}</option>`)
      .join("")}`;
    const first = sim.presets[0];
    const html = `
      <p class="notice">Simulated telegrams take the same path as real ones: log, decoding and entities. They are marked as simulated in the log.</p>
      <label class="field"><span>Telegram</span><select id="sim-preset">${presets}</select></label>
      <div class="row2">
        <label class="field"><span>Sender ID</span><input type="text" id="sim-sender" value="${esc(first?.default_sender || "")}" spellcheck="false" autocomplete="off"></label>
        <label class="field"><span>Or send it as</span><select id="sim-device">${deviceOptions}</select></label>
      </div>
      <div class="card-actions"><button class="btn primary" data-action="sim-send">${icon("mdi:send-outline")}Send telegram</button></div>
      <hr class="separator">
      <h3 style="margin-bottom:12px">Custom telegram</h3>
      <div class="row2">
        <label class="field"><span>Telegram type (RORG)</span><select id="sim-rorg">${RORG_CHOICES.map(([value, text]) => `<option value="${value}">${esc(text)}</option>`).join("")}</select></label>
        <label class="field"><span>Status byte</span><input type="text" id="sim-status" value="00" maxlength="2" spellcheck="false" autocomplete="off"></label>
      </div>
      <label class="field"><span>Data bytes</span><input type="text" id="sim-data" placeholder="00 00 76 08" spellcheck="false" autocomplete="off"><small>Hexadecimal, without RORG and sender. Spaces are fine.</small></label>
      <div class="row2">
        <label class="field"><span>Sender ID</span><input type="text" id="sim-custom-sender" value="05:00:00:99" spellcheck="false" autocomplete="off"></label>
        <label class="field"><span>Or send it as</span><select id="sim-custom-device">${deviceOptions}</select></label>
      </div>
      <div class="card-actions"><button class="btn" data-action="sim-custom">${icon("mdi:send-outline")}Send custom telegram</button></div>`;
    if (this._changed("sim", html)) body.innerHTML = html;
  }

  async _simulate(button) {
    const preset = this.$("#sim-preset").value;
    const sender = this.$("#sim-sender").value.trim();
    const result = await this._busy(button, () => this._api("POST", "simulate", { preset, sender }));
    if (!result) return;
    this._toast(`Sent from ${result.sent}: ${result.raw}`, "ok");
    this._loadTelegrams({ live: true });
  }

  async _simulateCustom(button) {
    const body = {
      rorg: this.$("#sim-rorg").value,
      status: this.$("#sim-status").value.trim() || "00",
      data: this.$("#sim-data").value.trim(),
      sender: this.$("#sim-custom-sender").value.trim(),
    };
    const result = await this._busy(button, () => this._api("POST", "simulate", body));
    if (!result) return;
    this._toast(`Sent from ${result.sent}: ${result.raw}`, "ok");
    this._loadTelegrams({ live: true });
  }

  // ── device types ──────────────────────────────────────────────────

  _renderTypes() {
    const chipsBox = this.$("#type-chips");
    const current = this._filters.typeCategory;
    const used = new Set(this._data.types.map((type) => type.category));
    const chips = `<button class="chip" data-action="type-category" data-value="" aria-pressed="${!current}">All</button>${this._data.categories
      .filter((category) => used.has(category.key))
      .map((category) => `<button class="chip" data-action="type-category" data-value="${esc(category.key)}" aria-pressed="${current === category.key}">${esc(category.name)}</button>`)
      .join("")}`;
    if (this._changed("type-chips", chips)) chipsBox.innerHTML = chips;
    const categoryOptions = `<option value="">All categories</option>${this._data.categories
      .filter((category) => used.has(category.key))
      .map((category) => `<option value="${esc(category.key)}">${esc(category.name)}</option>`)
      .join("")}`;
    const select = this.$("#type-category");
    if (this._changed("type-category", categoryOptions)) select.innerHTML = categoryOptions;
    select.value = current;

    const query = this._filters.typeSearch.trim().toLowerCase();
    const types = this._data.types.filter((type) =>
      (!current || type.category === current) &&
      (!query || [type.name, type.description, ...type.eeps].some((text) => text.toLowerCase().includes(query))),
    );
    this.$("#types-count").textContent = `${types.length} of ${plural(this._data.types.length, "type")}`;
    const html = types.length
      ? types.map((type) => this._typeCard(type)).join("")
      : `<div class="empty"><h3>No type matches</h3></div>`;
    const list = this.$("#types-list");
    if (this._changed("types", html)) list.innerHTML = html;
  }

  _typeCard(type) {
    const color = CATEGORY_COLORS[type.category] || "";
    const eeps = type.eeps.length > 8
      ? `${type.eeps.slice(0, 6).map((eep) => `<span class="pill mono">${eep}</span>`).join("")}<span class="pill">+${type.eeps.length - 6} more</span>`
      : type.eeps.map((eep) => `<span class="pill mono">${eep}</span>`).join("");
    const entities = type.entities
      .map((entity) => `<li>${esc(entity.kind.replace(/_/g, " "))}${entity.eeps.length ? ` (${entity.eeps.join(", ")} only)` : ""}${entity.on_demand ? " · appears when first reported" : ""}</li>`)
      .join("");
    return `
      <article class="card accent type-card" style="${color ? `--cat:${color}` : ""}">
        <div class="card-head">
          <span class="avatar">${icon(type.icon)}</span>
          <div class="head-text"><h3 title="${esc(type.name)}">${esc(type.name)}</h3><p class="muted">${esc(this._category(type.category))} · ${esc(type.family)}</p></div>
        </div>
        <div class="pills-row">
          ${type.verified ? pill("Tested on hardware", "ok", "mdi:check-decagram") : pill("From the specification", "", "mdi:file-document-outline")}
          ${type.usage ? pill(`Chosen ${plural(type.usage, "time")}`, "accent") : ""}
        </div>
        <p class="summary">${esc(type.description)}</p>
        <div class="eeps">${eeps}</div>
        <p class="muted" style="margin:0">${esc(PAIRING_TEXT[type.pairing] || type.pairing)}</p>
        <details><summary>Entities and pairing</summary><ul class="entity-list">${entities}</ul>${type.notes ? `<p>${esc(type.notes)}</p>` : ""}</details>
      </article>`;
  }

  // ── gateway ───────────────────────────────────────────────────────

  async _loadGateway() {
    try {
      this._data.gateway = await this._api("GET", "gateway");
      this._renderGateway();
    } catch (err) {
      this._toast(errorText(err), "error");
    }
  }

  _renderGateway() {
    const data = this._data.gateway;
    if (!data) return;
    const gateway = data.gateway;
    const address = data.address;
    const chip = gateway.chip;
    this._pairingUntil = data.pairing.open ? Date.now() + data.pairing.seconds * 1000 : 0;
    const state = gateway.ready
      ? pill("Ready", "ok")
      : gateway.connected
        ? pill("Reading the address", "warn")
        : pill("Offline", "bad");

    const status = `
      <article class="panel-card">
        <header>${icon("mdi:router-wireless")}<h3>Module</h3>${state}</header>
        ${gateway.last_error && !gateway.connected ? `<p class="notice bad">${esc(gateway.last_error)}. The integration tries again every 15 seconds.</p>` : ""}
        <dl class="kv">
          <dt>Serial port</dt><dd class="mono">${esc(gateway.port)}</dd>
          <dt>Module</dt><dd>${esc(chip?.description || "Unknown")}</dd>
          <dt>Firmware</dt><dd>${esc(chip?.app_version || "unknown")}${chip ? ` · API ${esc(chip.api_version)}` : ""}</dd>
          <dt>Chip ID</dt><dd class="mono">${esc(chip?.chip_id || "unknown")}</dd>
          <dt>Gateway address</dt><dd class="mono">${esc(gateway.base_id || "not read yet")}</dd>
          <dt>Address changes left</dt><dd>${gateway.remaining_writes ?? "the module did not say"}</dd>
          <dt>Frames sent</dt><dd>${gateway.sent_frames}</dd>
        </dl>
        <div class="card-actions">
          <button class="btn" data-action="gateway-refresh">${icon("mdi:refresh")}Read the address again</button>
          <button class="btn" data-action="reload">${icon("mdi:restart")}Reload the integration</button>
        </div>
      </article>`;

    const pairing = `
      <article class="panel-card">
        <header>${icon("mdi:link-variant")}<h3>Pairing</h3>${data.pairing.open ? `<span class="pill warn">Open · <span data-countdown></span></span>` : pill("Closed")}</header>
        <p>Two-way devices, such as switching actuators, shutter actuators and some buttons, pair by sending a request. The gateway answers only while pairing is open, or when the device is already adopted.</p>
        <div class="card-actions">
          ${data.pairing.open
            ? `<button class="btn" data-action="pairing-close">Close pairing</button>`
            : `<button class="btn primary" data-action="pairing-open">${icon("mdi:link-variant")}Open pairing for 3 minutes</button>`}
        </div>
      </article>`;

    const history = address.history.length
      ? `<ul class="history">${address.history
          .map((item) => `<li><span class="mono">${esc(item.from || "?")} → ${esc(item.to)}</span><br><span class="muted">${esc(dateText(item.at))}${item.remaining_after != null ? ` · ${item.remaining_after} left after it` : ""}${item.note ? ` · ${esc(item.note)}` : ""}</span></li>`)
          .join("")}</ul>`
      : `<p class="muted">No changes made from here.</p>`;

    const locked = `
      <p class="notice">Address changes are <strong>locked</strong>. The module allows only a few address changes in its whole life, usually 10, and one-way actuators obey the address they were taught with.</p>
      <div class="card-actions"><button class="btn" data-action="unlock">${icon("mdi:lock-open-variant-outline")}Unlock address changes…</button></div>`;
    const unlocked = `
      <p class="notice warn">Address changes are <strong>unlocked</strong>${address.unlocked_at ? ` since ${esc(dateText(address.unlocked_at))}` : ""}. Lock them again when you are done.</p>
      <label class="field"><span>New address</span><input type="text" id="address-new" placeholder="FF:A0:00:00" spellcheck="false" autocomplete="off">
        <small>Between FF:80:00:00 and FF:FF:FF:80, ending in 00 or 80.</small></label>
      <label class="field"><span>Note (optional)</span><input type="text" id="address-note" maxlength="200" placeholder="Why the address changes" autocomplete="off"></label>
      <div class="card-actions">
        <button class="btn primary" data-action="address-write">Change address…</button>
        ${address.uses_foreign_address ? `<button class="btn" data-action="address-restore">Back to the original address…</button>` : ""}
        <button class="btn ghost" data-action="lock">${icon("mdi:lock-outline")}Lock again</button>
      </div>`;

    const addressCard = `
      <article class="panel-card">
        <header>${icon("mdi:identifier")}<h3>Gateway address</h3>${address.unlocked ? pill("Unlocked", "warn") : pill("Locked")}</header>
        <dl class="kv">
          <dt>Current</dt><dd class="mono">${esc(address.current || gateway.base_id || "unknown")}</dd>
          <dt>Original</dt><dd><span class="mono">${esc(address.original || "not recorded")}</span>${address.original_seen_at ? `<br><span class="muted">first read on ${esc(dateText(address.original_seen_at))}</span>` : ""}</dd>
          <dt>Changes made here</dt><dd>${address.changes_made}</dd>
          <dt>One-way actuators</dt><dd>${address.one_way_actuators}</dd>
        </dl>
        ${address.uses_foreign_address ? `<p class="notice warn">The gateway does not use its original address. Another gateway with the same address would control the same one-way actuators.</p>` : ""}
        ${history}
        ${address.unlocked ? unlocked : locked}
      </article>`;

    const html = status + pairing + addressCard;
    const body = this.$("#gateway-body");
    if (this._changed("gateway", html)) {
      // Keep what the user is typing in the address form across refreshes.
      const typed = { address: this.$("#address-new")?.value, note: this.$("#address-note")?.value };
      body.innerHTML = html;
      if (typed.address && this.$("#address-new")) this.$("#address-new").value = typed.address;
      if (typed.note && this.$("#address-note")) this.$("#address-note").value = typed.note;
    }
    this._tick();
  }

  async _pairingWindow(button, action) {
    const result = await this._busy(button, () => this._api("POST", "gateway/pairing", { action, seconds: 180 }));
    if (!result) return;
    this._pairingUntil = result.pairing.open ? Date.now() + result.pairing.seconds * 1000 : 0;
    this._toast(result.pairing.open ? "Pairing is open for 3 minutes. Put the device into learn mode." : "Pairing is closed.", "ok");
    await this._loadGateway();
    this._refreshStatus();
  }

  async _reload(button) {
    const result = await this._busy(button, () => this._api("POST", "reload"));
    if (!result) return;
    this._toast("The integration is reloading.", "ok");
    setTimeout(() => {
      this._loadGateway();
      this._refreshStatus();
    }, 4000);
  }

  async _gatewayAddress(button, body, message) {
    const result = await this._busy(button, () => this._api("POST", "gateway/address", body));
    if (!result) return undefined;
    this._data.gateway = result;
    this._renderGateway();
    if (message) this._toast(message, "ok");
    return result;
  }

  _unlockDialog() {
    this._openDialog(
      `<header><h2 id="dlg-title">Unlock address changes?</h2></header>
       <div class="body">
         <p>The gateway address decides which one-way actuators obey this gateway. Changing it means:</p>
         <ul class="entity-list">
           <li>one-way actuators taught with the old address stop obeying until they are paired again;</li>
           <li>a gateway with the same address as another one controls the same actuators;</li>
           <li>the module allows only a few changes in its whole life, and ${RESERVED_TEXT}.</li>
         </ul>
       </div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">Unlock</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          const result = await this._gatewayAddress(button, { action: "unlock" }, "Address changes are unlocked.");
          if (result) this._closeDialog();
        },
      },
    );
  }

  _addressDialog(action) {
    const data = this._data.gateway;
    const gateway = data.gateway;
    const address = data.address;
    const target = action === "restore" ? address.original : (this.$("#address-new")?.value || "").trim().toUpperCase();
    const note = (this.$("#address-note")?.value || "").trim();
    if (!target) {
      this._toast("Enter the new address first.", "error");
      return;
    }
    const left = gateway.remaining_writes;
    const needsWord = address.needs_confirmation || left === null || left === undefined;
    const word = address.rules.confirmation_word;
    this._openDialog(
      `<header><h2 id="dlg-title">${action === "restore" ? "Go back to the original address?" : "Change the gateway address?"}</h2></header>
       <div class="body">
         <p class="mono" style="font-size:16px">${esc(gateway.base_id)} → ${esc(target)}</p>
         <ul class="entity-list">
           <li>${left != null ? `The module allows ${left} more ${left === 1 ? "change" : "changes"}; ${left - 1} will be left after this one.` : "The module did not say how many changes are left."}</li>
           <li>${address.one_way_actuators ? `${plural(address.one_way_actuators, "one-way actuator")} will stop obeying until paired again.` : "No one-way actuators are adopted."}</li>
           <li>Two-way devices are addressed by their own ID and keep working.</li>
         </ul>
         ${needsWord ? `<label class="field"><span>Type ${esc(word)} to confirm</span><input type="text" name="confirm" autocomplete="off" spellcheck="false"></label>` : ""}
       </div>
       <div class="actions">
         <button class="btn danger" data-dlg="confirm">${action === "restore" ? "Go back to the original" : "Change address"}</button>
         <button class="btn" data-dlg="close">Cancel</button>
       </div>`,
      {
        confirm: async (button) => {
          const confirm = this.$('.dialog [name="confirm"]')?.value.trim() || undefined;
          const body = { action, address: target, note: note || null, expected_current: gateway.base_id, confirm };
          const result = await this._busy(button, () => this._api("POST", "gateway/address", body));
          if (!result) return;
          this._closeDialog();
          this._data.gateway = result;
          this._signatures.gateway = null;
          this._renderGateway();
          if (result.success) {
            const input = this.$("#address-new");
            if (input) input.value = "";
            this._toast(`The gateway address is now ${result.current}.`, "ok");
          } else {
            this._toast(`The address did not change: ${result.reason}.`, "error");
          }
        },
      },
    );
  }
}

const RESERVED_TEXT = "the last 2 are kept to go back to the original address";

const CATEGORY_COLORS = {
  button: "#8e5bd8",
  contact: "#3f6fd8",
  window: "#3f6fd8",
  motion: "#1aa3b8",
  climate: "#2b8fd6",
  air: "#139a83",
  smoke: "#d24343",
  weather: "#4a7bd0",
  meter: "#3c9a4c",
  switch: "#e07b24",
  light: "#d9a400",
  cover: "#8a6a4e",
};

if (!customElements.get("enocean-chip-panel")) {
  customElements.define("enocean-chip-panel", EnoceanChipPanel);
}
