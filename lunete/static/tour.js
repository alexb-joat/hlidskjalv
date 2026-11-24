"use strict";
/* The starting tour and the Help menu. Loaded after app.js and built from its helpers (h, $, store, icon,
   showTab, showShortcuts, openModal). The tour puts a spotlight on one control at a time with a cloud that
   says what it is for; it runs once, after the first-start setup, and again from Help. */

const REPO = "https://github.com/alexb-joat/lunete";
ICONS["v-help"] = '<circle cx="12" cy="12" r="9"/><path d="M9.6 9.3a2.5 2.5 0 1 1 3.4 2.6c-.6.3-1 .8-1 1.5v.6"/><path d="M12 17.2v.1"/>';

const TOUR = [
  { at: () => $("#nav"), title: "Views", text: "Each icon opens a view: the live map, Replay, Learn, Details and Alerts. The number keys 1 to 9 open them too." },
  { view: "live", at: () => $("#world-slot .map"), title: "The live map", text: "Every folder is a group and every file a tile. A tile glows when you or your AI helper change it; click it for the whole story." },
  { view: "live", at: () => $("#world-slot .log"), title: "Activity", text: "Every edit, command, save point and AI step lands here, newest first. Hover over an entry to see what it means." },
  { at: () => $("#tab-alerts"), title: "Alerts", text: "Things you didn't do yourself: a risky command an AI ran, a startup file that changed, a refused request. The number counts the ones you haven't seen." },
  { at: () => $("#open-settings"), title: "Settings", text: "Colours and background, what Lunete watches, pairing this browser, and sync between your computers." },
  { at: () => $("#open-help"), title: "Help", text: "This tour, the keyboard shortcuts and what each view does, whenever you need them." },
];
let tour = null;

const shown = el => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };

function startTour() {
  endTour(false);
  closeHelp();
  if (document.querySelector(".modal-back")) return;
  const steps = TOUR.filter(s => s.view ? visibleViews().includes(s.view) && s.at() : shown(s.at()));
  if (!steps.length) return;
  hideTip();
  const count = h("div", { class: "tour-count", "aria-live": "polite" }), title = h("h3", { id: "tour-title" }), text = h("p", { id: "tour-text" });
  const back = h("button", { type: "button", class: "btn small", onclick: () => go(-1) }, "Back");
  const next = h("button", { type: "button", class: "btn small primary", onclick: () => go(1) }, "Next");
  const cloud = h("div", { class: "tour-cloud", role: "dialog", "aria-modal": "true", "aria-labelledby": "tour-title", "aria-describedby": "tour-text" },
    count, title, text, h("div", { class: "tour-foot" }, h("button", { type: "button", class: "btn small", onclick: () => endTour(true) }, "Skip tour"), h("span", { class: "sep" }), back, next));
  const layer = h("div", { class: "tour-layer", "aria-hidden": "true" }), hole = h("div", { class: "tour-hole", "aria-hidden": "true" });
  const dims = [0, 1, 2, 3].map(() => h("div", { class: "tour-dim", "aria-hidden": "true" }));  // above, below, left, right of the spotlight
  document.body.append(layer, ...dims, hole, cloud);
  document.documentElement.classList.add("tour-on");
  tour = { i: 0, steps, cloud, hole, layer, dims, count, title, text, back, next, prevFocus: document.activeElement };
  addEventListener("keydown", tourKeys, true);
  addEventListener("resize", placeTour);
  addEventListener("scroll", placeTour, true);
  paintTour();
}

function go(d) {
  if (!tour) return;
  const i = tour.i + d;
  if (i >= tour.steps.length) return endTour(true);
  if (i < 0) return;
  tour.i = i;
  paintTour();
}

function paintTour() {
  const s = tour.steps[tour.i], n = tour.steps.length;
  if (s.view && currentTab !== s.view) showTab(s.view);
  tour.count.textContent = `${tour.i + 1} of ${n}`;
  tour.title.textContent = s.title;
  tour.text.textContent = s.text;
  tour.back.disabled = tour.i === 0;
  tour.next.textContent = tour.i === n - 1 ? "Done" : "Next";
  const el = s.at();
  if (el) el.scrollIntoView({ block: "nearest" });
  placeTour();
  tour.next.focus();
}

/* The spotlight hugs the control (clipped to the window); the cloud sits below it, above it, or inside a
   control too tall for either, with its arrow pointing at the control's middle. */
function placeTour() {
  if (!tour) return;
  const el = tour.steps[tour.i].at();
  if (!el) return;
  const r = el.getBoundingClientRect(), pad = 6, gap = 14, vw = innerWidth, vh = innerHeight;
  const top = Math.max(4, r.top - pad), bottom = Math.min(vh - 4, r.bottom + pad);
  const left = r.left - pad, right = r.right + pad, box = (el, x, y, w, hh) => Object.assign(el.style, { left: x + "px", top: y + "px", width: Math.max(0, w) + "px", height: Math.max(0, hh) + "px" });
  box(tour.hole, left, top, right - left, bottom - top);
  const [above, below, before, after] = tour.dims;
  box(above, 0, 0, vw, top);
  box(below, 0, bottom, vw, vh - bottom);
  box(before, 0, top, left, bottom - top);
  box(after, right, top, vw - right, bottom - top);
  const c = tour.cloud, cw = c.offsetWidth, ch = c.offsetHeight;
  let y = bottom + gap, side = "below";
  if (y + ch > vh - 8) { y = top - gap - ch; side = "above"; }
  if (y < 8) { y = Math.min(vh - ch - 8, Math.max(8, top + 16)); side = "inside"; }
  const x = Math.min(Math.max(8, r.left + r.width / 2 - cw / 2), vw - cw - 8);
  c.style.left = x + "px";
  c.style.top = y + "px";
  c.dataset.side = side;
  c.style.setProperty("--arrow-x", Math.min(Math.max(18, r.left + r.width / 2 - x), cw - 18) + "px");
}

function tourKeys(e) {
  if (!tour) return;
  if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); endTour(true); }
  else if (e.key === "ArrowRight") { e.preventDefault(); e.stopImmediatePropagation(); go(1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopImmediatePropagation(); go(-1); }
  else if (e.key === "Tab") {  // focus stays in the cloud
    const f = [...tour.cloud.querySelectorAll("button")].filter(b => !b.disabled);
    const i = f.indexOf(document.activeElement);
    e.preventDefault();
    f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  } else if (!tour.cloud.contains(e.target) || !/^(Enter| )$/.test(e.key)) {
    e.stopImmediatePropagation();  // the page's own keys (1-9, ?, /) wait until the tour is over
  }
}

function endTour(done) {
  if (!tour) return;
  removeEventListener("keydown", tourKeys, true);
  removeEventListener("resize", placeTour);
  removeEventListener("scroll", placeTour, true);
  for (const el of [tour.layer, ...tour.dims, tour.hole, tour.cloud]) el.remove();
  document.documentElement.classList.remove("tour-on");
  const back = tour.prevFocus;
  tour = null;
  if (done) store.set("tour", { done: true, at: Date.now() });
  if (back && back.focus && back.isConnected) back.focus();
}

/* Once per browser: after the first-start setup closes, or at boot when setup was done before. */
function startTourOnce() {
  if (store.get("tour", null) || tour) return;
  requestAnimationFrame(() => requestAnimationFrame(startTour));
}

/* =========================================================== Help menu */
let helpMenu = null;
function helpButton() {
  return h("button", { type: "button", id: "open-help", "aria-label": "Help", "aria-haspopup": "menu", "aria-expanded": "false",
    tip: { title: "Help", body: "The tour, keyboard shortcuts, what each view does, and the guide." },
    onclick: e => helpMenu ? closeHelp() : openHelp(e.currentTarget, e.detail === 0),
    onkeydown: e => { if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); openHelp(e.currentTarget, true); } } },
  icon("v-help"), h("span", { class: "lbl", text: "Help" }));
}

function openHelp(btn, focusFirst) {
  closeHelp();
  hideTip();
  const item = (label, note, act, href) => h(href ? "a" : "button", href ? { role: "menuitem", href, target: "_blank", rel: "noopener noreferrer", tabindex: "-1", onclick: () => closeHelp() }
    : { role: "menuitem", type: "button", tabindex: "-1", onclick: () => { closeHelp(); act(); } }, h("span", { text: label }), note ? h("small", { text: note }) : null);
  const menu = h("div", { class: "help-menu", role: "menu", "aria-label": "Help", onkeydown: helpKeys },
    item("Take the tour", "A spotlight on each part of the page", () => startTour()),
    item("Keyboard shortcuts", "Or press ?", () => showShortcuts()),
    item("What each view does", null, () => showViewsHelp()),
    item("Command decoder", "What each part of a command means", () => { showTab("learn"); requestAnimationFrame(() => { const d = $("#dec-in"); if (d) d.focus(); }); }),
    h("hr"),
    item("Guide", "On GitHub", null, REPO + "/blob/main/docs/guide.md"),
    item("Report a problem", "On GitHub", null, REPO + "/issues/new"));
  document.body.append(menu);
  const r = btn.getBoundingClientRect(), mw = menu.offsetWidth, mh = menu.offsetHeight;
  const y = r.bottom + 6 + mh <= innerHeight ? r.bottom + 6 : Math.max(8, r.top - 6 - mh);
  menu.style.top = y + "px";
  menu.style.left = Math.min(Math.max(8, r.right - mw), innerWidth - mw - 8) + "px";
  btn.setAttribute("aria-expanded", "true");
  helpMenu = { menu, btn };
  setTimeout(() => addEventListener("pointerdown", helpOutside, true), 0);
  addEventListener("keydown", helpEscape, true);
  addEventListener("resize", closeHelp);
  const first = menu.querySelector('[role="menuitem"]');
  if (focusFirst) first.focus();
}

function helpKeys(e) {
  const items = [...helpMenu.menu.querySelectorAll('[role="menuitem"]')], i = items.indexOf(document.activeElement);
  const j = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key];
  if (j !== undefined) { e.preventDefault(); items[(j + items.length) % items.length].focus(); }
  else if (e.key === "Tab") closeHelp();
}

/* Esc closes the menu wherever focus is (after a mouse click it stays on the button). */
function helpEscape(e) {
  if (e.key !== "Escape" || !helpMenu) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const b = helpMenu.btn;
  closeHelp();
  b.focus();
}

function helpOutside(e) { if (helpMenu && !helpMenu.menu.contains(e.target) && !helpMenu.btn.contains(e.target)) closeHelp(); }

function closeHelp() {
  if (!helpMenu) return;
  removeEventListener("pointerdown", helpOutside, true);
  removeEventListener("keydown", helpEscape, true);
  removeEventListener("resize", closeHelp);
  helpMenu.btn.setAttribute("aria-expanded", "false");
  helpMenu.menu.remove();
  helpMenu = null;
}

function showViewsHelp() {
  const rows = [...visibleViews().map(v => [icon("v-" + v), VIEWS[v].label, VIEWS[v].tip]),
    [icon("v-settings"), "Settings", "Appearance, what Lunete watches, pairing, sync and this computer."],
    [icon("v-help"), "Help", "The tour, keyboard shortcuts and the guide."]];
  openModal("What each view does", h("ul", { class: "viewhelp" }, ...rows.map(([ic, label, tip]) => h("li", {}, ic, h("b", { text: label }), h("span", { class: "muted", text: tip })))));
}

// The view bar was first drawn before this file loaded: draw it again, now with the Help button.
if ($("#nav") && !$("#open-help")) applyLayout((PREFS || DEFAULT_PREFS).layout);
