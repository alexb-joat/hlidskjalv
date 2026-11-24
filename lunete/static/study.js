"use strict";
/* Study: what you're learning, time this week against a target, scores and pieces.
   Personal: it lives in ~/.lunete/graph/personal.db on this machine, and only a paired browser reads or
   writes it (/api/study). No streaks and no points: a missed day costs nothing. */
(() => {
const ST = { data: null, error: null, editing: null, showAll: false, line: store.get("study.line", null), link: false };
const SHOWN = 30;

VIEWS.study = { label: "Study", tip: "What you're learning: time this week against your target, scores and pieces.", onShow: load };

const pad = n => String(n).padStart(2, "0");
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const asDate = iso => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
const dayLabel = iso => iso === todayISO() ? "Today" : asDate(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
const shortDate = iso => asDate(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
function mins(m) {
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
const est = e => !e ? "" : e[0] === e[1] ? mins(e[0]) : `${Math.round(e[0] / 60)}–${Math.round(e[1] / 60)} h`;

async function api(body) {
  const r = await OWNER.post("/api/study", body);
  ST.data = r.summary; ST.error = null;
  return r;
}

async function load() {
  const slot = $("#study-body");
  if (!slot) return;
  if (!OWNER.key) { ST.data = null; ST.error = "unpaired"; return render(); }
  try {
    const r = await fetch("/api/study", { cache: "no-store", headers: { "X-Hlidskjalv-Key": OWNER.key } });
    const d = await r.json().catch(() => ({}));
    if (r.status === 401) { OWNER.forget(); ST.error = "unpaired"; ST.data = null; }
    else if (!r.ok) { ST.error = d.error || `HTTP ${r.status}`; }
    else { ST.data = d; ST.error = null; }
  } catch { ST.error = "Lost contact with Lunete. Is it still running?"; }
  render();
}

function say(text, bad) {
  const live = $("#study-status");
  if (live) { live.textContent = text; live.classList.toggle("bad", !!bad); }
}

function undoToast(text, undo) {
  const btn = h("button", { class: "btn", type: "button", text: "Undo" });
  const t = h("div", { class: "toast study-toast", role: "status" }, h("span", { text }), btn);
  btn.addEventListener("click", async () => { t.remove(); try { await undo(); render(); say("Undone."); } catch (e) { say(e.message, true); } });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 8000);
}

/* ----------------------------------------------------------- the log form */
function logForm(d) {
  const lines = d.lines;
  if (!lines.some(l => l.id === ST.line)) ST.line = lines[0].id;
  const line = lines.find(l => l.id === ST.line);
  const sel = h("select", { id: "study-line", "aria-label": "Line" }, lines.map(l => h("option", { value: l.id, selected: l.id === ST.line, text: l.name })));
  sel.addEventListener("change", () => { ST.line = sel.value; store.set("study.line", ST.line); render(); $("#study-line")?.focus(); });
  const time = h("input", { id: "study-time", inputmode: "text", autocomplete: "off", placeholder: "25m", "aria-label": "How long, like 25m or 1h", required: true });
  const day = h("input", { id: "study-day", type: "date", value: todayISO(), max: todayISO(), "aria-label": "Day" });
  const note = h("input", { id: "study-note", maxlength: 200, autocomplete: "off", placeholder: "What you did (optional)", "aria-label": "Note" });
  const scored = line.checks.length || line.scores.length;
  const score = scored ? h("input", { id: "study-score", type: "number", min: 0, max: 10, step: 1, inputmode: "numeric", placeholder: "0–10", "aria-label": "Score, 0 to 10 (optional)" }) : null;
  const pieces = line.pieces.filter(p => !p.done);
  const piece = pieces.length ? h("select", { id: "study-piece", "aria-label": "Piece (optional)" },
    h("option", { value: "", text: "No piece" }), pieces.map(p => h("option", { value: p.id, text: p.title }))) : null;
  const url = ST.link ? h("input", { id: "study-url", type: "url", placeholder: "https://…", "aria-label": "Link (optional)" }) : null;
  const linkBtn = h("button", { class: "linkish", type: "button", "aria-expanded": ST.link ? "true" : "false", text: ST.link ? "No link" : "Add a link" });
  linkBtn.addEventListener("click", () => { ST.link = !ST.link; render(); $(ST.link ? "#study-url" : "#study-time")?.focus(); });
  const form = h("form", { class: "panel study-log", "aria-label": "Log a session" },
    h("div", { class: "study-row" },
      h("label", { class: "study-field grow" }, h("span", { text: "Line" }), sel),
      h("label", { class: "study-field time" }, h("span", { text: "Time" }), time),
      h("label", { class: "study-field" }, h("span", { text: "Day" }), day),
      h("button", { class: "btn primary", type: "submit", text: "Log it" })),
    h("div", { class: "study-row" },
      h("label", { class: "study-field grow" }, h("span", { text: "Note" }), note),
      score && h("label", { class: "study-field score" }, h("span", { text: "Score" }), score),
      piece && h("label", { class: "study-field" }, h("span", { text: "Piece" }), piece),
      url && h("label", { class: "study-field grow" }, h("span", { text: "Link" }), url),
      linkBtn),
    h("p", { id: "study-status", class: "study-status", "aria-live": "polite" }));
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const body = { action: "log", line: ST.line, minutes: time.value.trim(), day: day.value || todayISO(), note: note.value };
    if (score && score.value !== "") body.score = score.value;
    if (piece && piece.value) body.piece = piece.value;
    if (url && url.value.trim()) body.url = url.value.trim();
    try {
      const r = await api(body);
      render();
      const l = ST.data.lines.find(x => x.id === body.line);
      say(`Logged ${mins(ST.data.entries.find(x => x.id === r.entry)?.minutes || 0)} on ${l ? l.name : "the line"}.` +
          (l && l.done_for_week ? " That's the week's target: done for this week." : ""));
      $("#study-time")?.focus();
    } catch (err) { say(err.message, true); }
  });
  return form;
}

/* ----------------------------------------------------------- a line's card */
function weekBars(line) {
  const top = Math.max(line.week_min, ...line.weeks.map(w => w.minutes), 1);
  const t = line.week_min ? 100 - (line.week_min / top) * 100 : null;
  return h("div", { class: "study-weeks", role: "img", "aria-label": `Last ${line.weeks.length} weeks: ` + line.weeks.map(w => `${shortDate(w.start)} ${mins(w.minutes)}`).join(", ") },
    t != null && h("i", { class: "target", style: `top:${t.toFixed(1)}%`, "aria-hidden": "true" }),
    line.weeks.map((w, i) => h("b", { class: i === line.weeks.length - 1 ? "now" : null, style: `height:${Math.max(w.minutes ? 4 : 0, (w.minutes / top) * 100).toFixed(1)}%`,
      tip: `Week of ${shortDate(w.start)}: ${mins(w.minutes)}${line.week_min ? ` of ${mins(line.week_min)}` : ""}` })));
}

function scoreChart(line) {
  const today = todayISO();
  const nextCheck = line.checks.find(c => c >= today);
  if (!line.scores.length) {
    return h("p", { class: "muted study-small", text: nextCheck ? `No scores yet. Next check: ${shortDate(nextCheck)}.` : "No scores yet." });
  }
  const pts = line.scores, days = pts.map(p => p.day).concat(line.checks);
  const t0 = asDate(days.reduce((a, b) => a < b ? a : b)).getTime(), t1 = asDate(days.reduce((a, b) => a > b ? a : b)).getTime();
  const W = 300, H = 64, P = 7, span = Math.max(t1 - t0, 864e5);
  const x = iso => P + ((asDate(iso).getTime() - t0) / span) * (W - 2 * P);
  const y = s => H - P - (s / 10) * (H - 2 * P);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "study-scores"); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Scores: " + pts.map(p => `${shortDate(p.day)} ${p.score} of 10`).join(", ") + (line.checks.length ? ". Checks: " + line.checks.map(shortDate).join(", ") : ""));
  const add = (tag, attrs) => { const el = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); svg.append(el); return el; };
  add("line", { x1: P, x2: W - P, y1: y(5), y2: y(5), class: "mid" });
  for (const c of line.checks) { add("line", { x1: x(c), x2: x(c), y1: P / 2, y2: H - P / 2, class: "check" }); }
  if (pts.length > 1) add("polyline", { points: pts.map(p => `${x(p.day).toFixed(1)},${y(p.score).toFixed(1)}`).join(" "), class: "path" });
  for (const p of pts) add("circle", { cx: x(p.day).toFixed(1), cy: y(p.score).toFixed(1), r: 3.5, class: "dot" });
  const last = pts[pts.length - 1];
  return h("div", { class: "study-chart" }, svg,
    h("p", { class: "muted study-small" }, `Last: ${last.score} of 10 on ${shortDate(last.day)}`, nextCheck ? ` · next check ${shortDate(nextCheck)}` : "", line.checks.length ? " (dashed lines)" : ""));
}

function piecesList(line) {
  if (!line.pieces.length) return null;
  return h("ul", { class: "study-pieces", "aria-label": `${line.name} pieces` }, line.pieces.map(p => {
    const box = h("input", { type: "checkbox", checked: p.done, "aria-label": `${p.title} done` });
    box.addEventListener("change", async () => {
      try { await api({ action: "piece", piece: p.id, done: box.checked }); render(); say(`${p.title}: ${box.checked ? "done" : "open again"}.`); }
      catch (e) { box.checked = !box.checked; say(e.message, true); }
    });
    const hi = p.est_min ? p.est_min[1] : 0;
    return h("li", { class: p.done ? "done" : null },
      h("label", {}, box, h("span", { class: "t", text: p.title })),
      h("span", { class: "n" }, mins(p.minutes), p.est_min ? h("span", { class: "muted", text: ` of ${est(p.est_min)}` }) : ""),
      hi ? h("div", { class: "bar", "aria-hidden": "true" }, h("i", { style: `width:${Math.min(100, (p.minutes / hi) * 100).toFixed(1)}%` })) : null);
  }));
}

function targetControl(line) {
  if (ST.editing === "target:" + line.id) {
    const inp = h("input", { value: mins(line.week_min).replace(" min", "m").replace(" h ", "h").replace(" h", "h"), "aria-label": `Weekly target for ${line.name}, like 6h`, size: 6 });
    const f = h("form", { class: "study-inline" }, inp, h("button", { class: "btn", type: "submit", text: "Save" }),
      h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { ST.editing = null; render(); } }));
    f.addEventListener("submit", async e => {
      e.preventDefault();
      try { await api({ action: "target", line: line.id, week: inp.value.trim() }); ST.editing = null; render(); say(`${line.name}: ${mins(line.week_min)} → ${mins(ST.data.lines.find(l => l.id === line.id).week_min)} a week.`); }
      catch (err) { say(err.message, true); }
    });
    queueMicrotask(() => inp.focus());
    return f;
  }
  return h("button", { class: "linkish", type: "button", text: line.week_min ? `Target ${mins(line.week_min)} a week · change` : "Set a weekly target",
    onclick: () => { ST.editing = "target:" + line.id; render(); } });
}

function lineCard(line) {
  const pct = line.week_min ? Math.min(100, (line.this_week / line.week_min) * 100) : 0;
  const left = Math.max(0, line.week_min - line.this_week);
  return h("article", { class: "panel study-card", "aria-label": line.name },
    h("header", {},
      h("h3", { text: line.name }),
      line.done_for_week ? h("span", { class: "chip ok" }, "✓ ", "done for this week")
        : line.week_min ? h("span", { class: "chip no", text: `${mins(left)} to go` }) : null),
    h("p", { class: "study-big" }, h("b", { text: mins(line.this_week) }), line.week_min ? h("span", { class: "muted", text: ` of ${mins(line.week_min)} this week` }) : h("span", { class: "muted", text: " this week" })),
    line.week_min ? h("div", { class: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": line.week_min, "aria-valuenow": Math.min(line.this_week, line.week_min), "aria-label": `${line.name} this week` },
      h("i", { style: `width:${pct.toFixed(1)}%` })) : null,
    h("p", { class: "muted study-small", text: line.today ? `Today: ${mins(line.today)}` : "Nothing today yet. A missed day costs nothing." }),
    weekBars(line),
    (line.checks.length || line.scores.length) ? scoreChart(line) : null,
    piecesList(line),
    h("footer", {}, targetControl(line), h("span", { class: "muted study-small", text: `${mins(line.total)} in all` })));
}

/* ----------------------------------------------------------- entries */
function entryRow(e, names) {
  if (ST.editing === "entry:" + e.id) {
    const time = h("input", { value: String(e.minutes), "aria-label": "Minutes", size: 5 });
    const note = h("input", { value: e.note, maxlength: 200, "aria-label": "Note" });
    const score = h("input", { type: "number", min: 0, max: 10, value: e.score ?? "", "aria-label": "Score, 0 to 10, empty for none", size: 3 });
    const day = h("input", { type: "date", value: e.day, max: todayISO(), "aria-label": "Day" });
    const f = h("form", { class: "study-entry editing" }, day, time, note, score,
      h("button", { class: "btn primary", type: "submit", text: "Save" }),
      h("button", { class: "btn", type: "button", text: "Cancel", onclick: () => { ST.editing = null; render(); } }));
    f.addEventListener("submit", async ev => {
      ev.preventDefault();
      try { await api({ action: "edit", entry: e.id, day: day.value, minutes: time.value.trim(), note: note.value, score: score.value === "" ? null : score.value }); ST.editing = null; render(); say("Entry saved."); }
      catch (err) { say(err.message, true); }
    });
    queueMicrotask(() => time.focus());
    return h("li", {}, f);
  }
  const del = h("button", { class: "linkish", type: "button", text: "Delete", "aria-label": `Delete ${mins(e.minutes)} on ${dayLabel(e.day)}` });
  del.addEventListener("click", async () => {
    try { await api({ action: "delete", entry: e.id }); render(); undoToast(`Deleted ${mins(e.minutes)} on ${names[e.line] || "a line"}.`, () => api({ action: "restore", entry: e.id })); }
    catch (err) { say(err.message, true); }
  });
  return h("li", { class: "study-entry" },
    h("span", { class: "d", text: dayLabel(e.day) }),
    h("span", { class: "chip", text: names[e.line] || "?" }),
    h("span", { class: "m", text: mins(e.minutes) }),
    h("span", { class: "note" }, e.note || h("span", { class: "muted", text: "no note" }),
      e.url ? h("a", { href: e.url, target: "_blank", rel: "noopener noreferrer", class: "study-url", text: " link" }) : null),
    e.score != null ? h("span", { class: "chip", text: `${e.score}/10` }) : h("span"),
    h("span", { class: "acts" },
      h("button", { class: "linkish", type: "button", text: "Edit", "aria-label": `Edit ${mins(e.minutes)} on ${dayLabel(e.day)}`, onclick: () => { ST.editing = "entry:" + e.id; render(); } }),
      del));
}

function entriesPanel(d) {
  const names = Object.fromEntries(d.lines.map(l => [l.id, l.name]));
  const list = ST.showAll ? d.entries : d.entries.slice(0, SHOWN);
  return h("section", { class: "panel study-entries", "aria-label": "Logged sessions" },
    h("header", {}, h("h3", { text: "Logged" }), h("span", { class: "muted study-small", text: d.entry_count === 1 ? "1 session" : `${d.entry_count} sessions` }),
      h("button", { class: "btn", type: "button", text: "Download CSV", onclick: downloadCsv })),
    list.length ? h("ul", {}, list.map(e => entryRow(e, names)))
      : h("p", { class: "muted", text: "Nothing logged yet. Ten minutes of audio on a walk counts." }),
    d.entries.length > SHOWN ? h("button", { class: "linkish", type: "button", "aria-expanded": ST.showAll ? "true" : "false",
      text: ST.showAll ? "Show fewer" : `Show all ${d.entries.length}`, onclick: () => { ST.showAll = !ST.showAll; render(); } }) : null);
}

async function downloadCsv() {
  try {
    const r = await fetch("/api/study.csv", { cache: "no-store", headers: { "X-Hlidskjalv-Key": OWNER.key || "" } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    const url = URL.createObjectURL(await r.blob());
    const a = h("a", { href: url, download: `lunete-study-${todayISO()}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    say("CSV downloaded: every session, one row each.");
  } catch (e) { say(e.message, true); }
}

/* ----------------------------------------------------------- adding a line */
function newLineForm(first) {
  const name = h("input", { id: "study-new-name", maxlength: 80, placeholder: "Serbian", "aria-label": "What you're learning", required: true, autocomplete: "off" });
  const week = h("input", { id: "study-new-week", placeholder: "6h", "aria-label": "Weekly target, like 6h or 90m", size: 6 });
  const f = h("form", { class: first ? "panel study-start" : "study-inline study-add" },
    first ? h("h3", { text: "Start a study line" }) : null,
    first ? h("p", { class: "muted", text: "Name what you're learning and the time a week you mean to give it. Missing a day costs nothing." }) : null,
    h("div", { class: "study-row" },
      h("label", { class: "study-field grow" }, h("span", { text: "Learning" }), name),
      h("label", { class: "study-field time" }, h("span", { text: "Each week" }), week),
      h("button", { class: first ? "btn primary" : "btn", type: "submit", text: "Add line" })),
    first ? h("p", { class: "muted study-small" }, "Or from a terminal: ", h("code", { text: 'lunete study add-line "Serbian" --week 6h' })) : null,
    first ? h("p", { id: "study-status", class: "study-status", "aria-live": "polite" }) : null);
  f.addEventListener("submit", async e => {
    e.preventDefault();
    try { const r = await api({ action: "line", name: name.value.trim(), week: week.value.trim() || "0" }); ST.line = r.line; store.set("study.line", ST.line); ST.editing = null; render(); say(`Added ${name.value.trim()}.`); }
    catch (err) { say(err.message, true); }
  });
  return f;
}

/* ----------------------------------------------------------- the view */
function render() {
  const slot = $("#study-body");
  if (!slot) return;
  if (ST.error === "unpaired") {
    return fill(slot, h("div", { class: "panel study-start" }, h("h3", { text: "Pair this browser to see your study log" }),
      h("p", { class: "muted", text: "Study lines are personal. They stay on this computer, and only a paired browser can read them." }),
      h("p", { class: "muted study-small", text: PAIR_HINT })));
  }
  if (ST.error) {
    return fill(slot, h("div", { class: "panel study-start" }, h("h3", { text: "Study couldn't load" }), h("p", { class: "muted", text: ST.error }),
      h("button", { class: "btn", type: "button", text: "Try again", onclick: load })));
  }
  const d = ST.data;
  if (!d) return fill(slot, h("p", { class: "muted", text: "Loading…" }));
  if (!d.lines.length) return fill(slot, newLineForm(true));
  const adding = ST.editing === "new-line";
  fill(slot,
    logForm(d),
    h("div", { class: "study-cards" }, d.lines.map(lineCard)),
    entriesPanel(d),
    h("div", { class: "study-foot" }, adding ? newLineForm(false)
      : h("button", { class: "linkish", type: "button", text: "Add a study line", onclick: () => { ST.editing = "new-line"; render(); queueMicrotask(() => $("#study-new-name")?.focus()); } })));
}

/* The view bar was drawn before this file loaded: redraw it so Study gets its tab. */
if ($("#nav")) applyLayout((PREFS || DEFAULT_PREFS).layout);
})();
