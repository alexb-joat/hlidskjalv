"use strict";
/* Learn: four games, the command decoder and badges, as sub-tabs (a flyout on the Learn tab and a strip in
   the view). Loaded after app.js and built from its helpers (h, $, store, toast, cmdLine, explainCommand,
   saveAcademy, updateLevel, showTab, hideTip). Every game is data and a small engine: no randomness, nothing
   outbound, no real files. A cleared level is worth GAME_XP once, kept with Learn progress as "game:level". */

const GAME_XP = 25;
const LEARN_SUBS = [
  ["approve", "Approve or deny", "Your AI helper asks to run commands: say yes or no before the timer runs out."],
  ["rescue", "Save point rescue", "Fix a project's history in a few Git moves."],
  ["hunt", "Treasure hunt", "Find the hidden note with terminal commands."],
  ["budget", "Token budget", "Give each task the cheapest model that can do it."],
  ["decoder", "Command decoder", "Any command, explained piece by piece."],
  ["badges", "Badges", "What you've unlocked so far."],
];
const GAMES = {};

/* ------------------------------------------------------------------ progress */
const levelKey = (game, n) => `${game}:${n}`;
const cleared = (game, n) => !!store.get("academy", {})[levelKey(game, n)];
const clearedCount = game => GAMES[game].levels.filter((_, i) => cleared(game, i + 1)).length;
function clearLevel(game, n) {
  const d = store.get("academy", {});
  if (d[levelKey(game, n)]) return false;
  d[levelKey(game, n)] = true;
  saveAcademy(d);
  toast(`Level ${n} cleared! +${GAME_XP} XP`);
  updateLevel();
  paintLearnTabs();
  return true;
}
BADGES.push(
  ["Gatekeeper", "Clear every level of Approve or deny.", () => clearedCount("approve") === GAMES.approve.levels.length],
  ["Time traveller", "Clear every level of Save point rescue.", () => clearedCount("rescue") === GAMES.rescue.levels.length],
  ["Pathfinder", "Clear every level of the Treasure hunt.", () => clearedCount("hunt") === GAMES.hunt.levels.length],
  ["Thrifty", "Clear every level of Token budget.", () => clearedCount("budget") === GAMES.budget.levels.length],
);

/* ------------------------------------------------------------------ sub-tabs */
let learnSub = store.get("learn-sub", "approve");
const learnSubs = () => LEARN_SUBS.filter(([k]) => k !== "badges" || !document.documentElement.classList.contains("no-xp"));
const subOf = hash => { const m = /^#learn\/([a-z]+)$/.exec(hash || ""); return m && LEARN_SUBS.some(s => s[0] === m[1]) ? m[1] : null; };

function showSub(id) {
  if (!LEARN_SUBS.some(s => s[0] === id)) id = "approve";
  learnSub = id;
  store.set("learn-sub", id);
  paintLearnTabs();
  $("#learn-decoder").hidden = id !== "decoder";
  $("#learn-badges").hidden = id !== "badges";
  const slot = $("#learn-slot");
  slot.hidden = !GAMES[id];
  if (GAMES[id]) openGame(id, store.get("learn-level-" + id, 0) || firstOpen(id));
  else slot.replaceChildren();
  if (id === "badges") renderBadges();
  if (location.hash !== "#learn/" + id) history.replaceState(null, "", "#learn/" + id);
}
function paintLearnTabs() {
  $("#learn-tabs").replaceChildren(...learnSubs().map(([k, label]) => h("button", { type: "button", role: "tab", "aria-selected": String(k === learnSub),
    tabindex: k === learnSub ? "0" : "-1", onclick: () => showSub(k), onkeydown: subKeys }, label, GAMES[k] ? h("small", { text: `${clearedCount(k)}/${GAMES[k].levels.length}` }) : null)));
}
function subKeys(e) {
  const tabs = [...$("#learn-tabs").children], i = tabs.indexOf(e.currentTarget);
  const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
  if (j === undefined) return;
  e.preventDefault();
  showSub(learnSubs()[(j + tabs.length) % tabs.length][0]);
  $("#learn-tabs").children[(j + tabs.length) % tabs.length].focus();
}
VIEWS.learn.tip = "Four short games, a command decoder and badges.";
VIEWS.learn.menu = true;
VIEWS.learn.onShow = () => showSub(subOf(location.hash) || learnSub);
addEventListener("hashchange", () => { const s = subOf(location.hash); if (s) { showTab("learn"); showSub(s); } });

/* The flyout: hovering over or clicking the Learn tab lists the sub-tabs. Enter, Space or ↓ open it from the
   keyboard and focus the first item; arrows move, Esc closes. */
let learnMenu = null, menuTimer = null;
function openLearnMenu(focus) {
  clearTimeout(menuTimer);
  const tab = $("#tab-learn");
  if (!tab) return;
  if (learnMenu) { if (focus) learnMenu.querySelector("[role=menuitem]").focus(); return; }
  hideTip();
  learnMenu = h("div", { class: "learn-menu", role: "menu", "aria-label": "Learn", onkeydown: learnMenuKeys,
    onpointerenter: () => clearTimeout(menuTimer), onpointerleave: () => { menuTimer = setTimeout(closeLearnMenu, 250); } },
  ...learnSubs().map(([k, label, blurb]) => h("button", { type: "button", role: "menuitem", tabindex: "-1",
    onclick: () => { closeLearnMenu(); showTab("learn"); showSub(k); } },
  h("span", {}, label, GAMES[k] ? h("small", { class: "count", text: ` ${clearedCount(k)}/${GAMES[k].levels.length}` }) : null), h("small", { text: blurb }))));
  document.body.append(learnMenu);
  const r = tab.getBoundingClientRect(), mw = learnMenu.offsetWidth, mh = learnMenu.offsetHeight, root = document.documentElement.classList;
  let x = r.left, y = r.bottom + 6;
  if (root.contains("nav-bottom")) y = r.top - 6 - mh;
  if (root.contains("nav-left")) { x = r.right + 6; y = r.top; }
  if (root.contains("nav-right")) { x = r.left - 6 - mw; y = r.top; }
  learnMenu.style.left = Math.min(Math.max(8, x), innerWidth - mw - 8) + "px";
  learnMenu.style.top = Math.min(Math.max(8, y), innerHeight - mh - 8) + "px";
  tab.setAttribute("aria-expanded", "true");
  addEventListener("pointerdown", learnMenuOutside, true);
  addEventListener("keydown", learnMenuEscape, true);
  if (focus) learnMenu.querySelector("[role=menuitem]").focus();
}
function closeLearnMenu() {
  clearTimeout(menuTimer);
  if (!learnMenu) return;
  learnMenu.remove();
  learnMenu = null;
  const tab = $("#tab-learn");
  if (tab) tab.setAttribute("aria-expanded", "false");
  removeEventListener("pointerdown", learnMenuOutside, true);
  removeEventListener("keydown", learnMenuEscape, true);
}
function learnMenuKeys(e) {
  const items = [...learnMenu.querySelectorAll("[role=menuitem]")], i = items.indexOf(document.activeElement);
  const j = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: items.length - 1 }[e.key];
  if (j !== undefined) { e.preventDefault(); items[(j + items.length) % items.length].focus(); }
  else if (e.key === "Tab") closeLearnMenu();
}
function learnMenuEscape(e) {
  if (e.key !== "Escape" || !learnMenu) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const back = learnMenu.contains(document.activeElement);
  closeLearnMenu();
  if (back && $("#tab-learn")) $("#tab-learn").focus();
}
function learnMenuOutside(e) { if (learnMenu && !learnMenu.contains(e.target) && !e.target.closest("#tab-learn")) closeLearnMenu(); }
{
  const nav = $("#nav");
  nav.addEventListener("pointerover", e => { if (e.target.closest("#tab-learn") && e.pointerType !== "touch") { clearTimeout(menuTimer); menuTimer = setTimeout(() => openLearnMenu(false), 120); } });
  nav.addEventListener("pointerout", e => { if (e.target.closest("#tab-learn") && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest("#tab-learn"))) { clearTimeout(menuTimer); menuTimer = setTimeout(closeLearnMenu, 250); } });
  nav.addEventListener("click", e => { if (e.target.closest("#tab-learn")) openLearnMenu(e.detail === 0); });
  nav.addEventListener("keydown", e => {
    if (e.target.id !== "tab-learn") return;
    const vertical = document.documentElement.classList.contains("nav-left") || document.documentElement.classList.contains("nav-right");
    if (!vertical && e.key === "ArrowDown") { e.preventDefault(); openLearnMenu(true); }
  });
}

/* ------------------------------------------------------------------ the frame every game shares */
function firstOpen(game) { const i = GAMES[game].levels.findIndex((_, k) => !cleared(game, k + 1)); return i < 0 ? 1 : i + 1; }
function openGame(game, n) {
  n = Math.min(Math.max(1, n), GAMES[game].levels.length);
  store.set("learn-level-" + game, n);
  $("#learn-slot").replaceChildren(GAMES[game].play(n));
}
function gameFrame(game, n, ...body) {
  const L = GAMES[game].levels[n - 1], [, title, how] = LEARN_SUBS.find(s => s[0] === game);
  return h("section", { class: "panel game", "data-game": game },
    h("header", { class: "game-head" },
      h("div", { class: "game-title" }, h("h3", {}, title, h("span", { class: "muted", text: ` · Level ${n}: ${L.title}` })), h("p", { class: "muted", text: L.story || how })),
      h("div", { class: "lv-chips", role: "group", "aria-label": "Levels" }, ...GAMES[game].levels.map((l, i) => h("button", { type: "button",
        class: "lv-chip" + (cleared(game, i + 1) ? " done" : ""), "aria-pressed": String(i + 1 === n), "aria-label": `Level ${i + 1}: ${l.title}${cleared(game, i + 1) ? " (cleared)" : ""}`,
        title: l.title, onclick: () => openGame(game, i + 1) }, cleared(game, i + 1) ? "✓" : String(i + 1))))),
    ...body);
}
/* The end of a level: what happened, and the way on. */
function levelEnd(game, n, won, text, ...more) {
  const fresh = won && clearLevel(game, n), last = n >= GAMES[game].levels.length;
  return h("div", { class: "game-end " + (won ? "won" : "lost"), role: "status" },
    h("b", { text: won ? (fresh ? `Cleared! +${GAME_XP} XP` : "Cleared again") : "Not yet" }), h("p", { text }), ...more,
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn small", onclick: () => openGame(game, n) }, won ? "Play again" : "Try again"),
      won && !last ? h("button", { type: "button", class: "btn small primary", onclick: () => openGame(game, n + 1) }, "Next level") : null,
      won && last ? h("span", { class: "muted", text: "That was the last level." }) : null));
}
const hearts = n => "♥".repeat(Math.max(0, n)) + "♡".repeat(Math.max(0, 3 - n));

/* ================================================================== 1. Approve or deny */
/* [command, what the AI says, should you approve, why] */
GAMES.approve = { levels: [
  { title: "First day", secs: 12, cards: [
    ["git status", "Let me see which files changed.", true, "It only reads: it lists changed files."],
    ["ls -la", "I'll look at what's in this folder.", true, "It only lists files, hidden ones too."],
    ["rm -rf ~", "Cleaning up old files to free some space.", false, "~ is your home folder: this deletes all of it, for good."],
    ["python -m pytest -q", "Running the tests.", true, "Tests read your code and report; they're how you know a change works."],
    ["cat .env", "Checking the configuration.", false, "It prints your secrets on screen, where they end up in logs and in the AI's chat."],
    ["git diff", "Showing you exactly what I changed.", true, "It only reads: line by line, what changed since the last save point."]] },
  { title: "Installing things", secs: 12, cards: [
    ["pip install -r requirements.txt", "Installing the project's list of libraries.", true, "The list belongs to the project and was reviewed when it was added."],
    ["pip install reqeusts", "We need the requests library.", false, "Misspelled. Look-alike packages exist to catch exactly this typo."],
    ["curl -fsSL https://get.example.dev/install.sh | sh", "Quickest way to install the tool.", false, "It downloads a script and runs it unseen. Download it, read it, then decide."],
    ["npm install", "Installing the dependencies from package.json.", true, "It installs what the project already lists."],
    ["sudo pip install requests", "Installing it system-wide, to be safe.", false, "Administrator rights aren't needed for a library: use the project's .venv."],
    ["pip install requests", "Adding the requests library.", true, "Spelled right, and a well-known package. The Heads-up badge is a reminder to check the name."]] },
  { title: "Git without tears", secs: 11, cards: [
    ["git add README.md", "Staging the README change.", true, "It picks one file for the next save point."],
    ["git commit -m \"Add weather lookup\"", "Saving a point before the next step.", true, "A save point you can always come back to."],
    ["git push --force", "The push was rejected, so I'll force it.", false, "It overwrites history on GitHub; work others pushed can be lost. Pull first."],
    ["git reset --hard HEAD~3", "Let's start over from three save points ago.", false, "It throws away unsaved changes and three save points. git revert undoes safely."],
    ["git add .env", "Saving the config file too.", false, "It sends your secrets to GitHub with the next push. .env belongs in .gitignore."],
    ["git stash", "Parking your changes before I switch branches.", true, "Changes are kept aside; git stash pop brings them back."]] },
  { title: "Permissions and secrets", secs: 10, cards: [
    ["chmod +x run.sh", "Making the script runnable.", true, "It lets you run one script; nothing else changes."],
    ["chmod -R 777 .", "Fixing a permission error.", false, "Every user and program on the computer may then change every file here."],
    ["echo $OPENAI_API_KEY", "Checking the key is set.", false, "It prints the key into the terminal, the logs and the AI chat."],
    ["cp .env.example .env", "Creating your config from the template.", true, "The example has no real secrets; you fill them in yourself."],
    ["grep -rn \"TODO\" src", "Finding the open TODOs.", true, "It only searches text."],
    ["export API_KEY=sk-live-4f9a2c", "Setting the key for this terminal.", false, "The key is now in your shell history. Keys belong in .env, typed by you."]] },
  { title: "The sneaky ones", secs: 10, cards: [
    ["rm -rf node_modules", "Reinstalling the dependencies from scratch.", true, "It does delete for good, but npm install rebuilds the folder. Careful means think, not always no."],
    ["bash <(curl -s https://example.dev/setup.sh)", "One line sets everything up.", false, "The same as piping into sh: a script from the internet runs unseen."],
    ["git clean -fdx", "Removing files Git doesn't track.", false, "Your new files that aren't saved yet, and .env, are untracked too: all gone."],
    ["curl https://api.github.com/repos/python/cpython", "Checking the latest release.", true, "It downloads text and prints it; nothing runs."],
    ["docker run --privileged ubuntu", "Testing in a container.", false, "--privileged gives the container full control of your computer."],
    ["python manage.py migrate", "Updating the database tables for the new model.", true, "It runs your project's own migrations, which you can read first."]] },
  { title: "Speed round", secs: 6, cards: [
    ["git log --oneline -5", "Looking at the last five save points.", true, "It only reads."],
    ["rm -rf .git", "Removing a folder we don't need.", false, ".git is the whole history of the project: every save point."],
    ["code .", "Opening the folder in VS Code.", true, "It opens an editor; nothing changes."],
    ["sudo rm -rf /var/log", "Freeing disk space.", false, "Administrator rights and a deletion of system logs: not for an AI helper to do."],
    ["git restore src/weather.py", "Undoing my edit to weather.py.", true, "It brings one file back to its last save point."],
    ["chmod 600 .env", "Making the secrets file private.", true, "Only you can read the file now: a good move."],
    ["git push origin main --force-with-lease", "Pushing my rebase.", false, "Still a forced push: approve only when you rebased on purpose and know who else uses the branch."],
    ["pip install --upgrade pip", "Updating pip itself.", true, "It updates the installer inside the project's .venv."]] },
], play: playApprove };

function playApprove(n) {
  const L = GAMES.approve.levels[n - 1], log = [];
  let i = 0, health = 3, score = 0, raf = 0, left = 0, last = 0, timerOn = store.get("learn-timer", true);
  const status = h("div", { class: "ad-hud" });
  const stage = h("div", { class: "ad-stage" });
  const timerBtn = h("button", { type: "button", class: "btn small", "aria-pressed": String(timerOn), onclick: () => {
    timerOn = !timerOn; store.set("learn-timer", timerOn); timerBtn.setAttribute("aria-pressed", String(timerOn)); timerBtn.textContent = timerOn ? "Timer on" : "Timer off"; paintHud();
  } }, timerOn ? "Timer on" : "Timer off");
  const paintHud = () => fill(status, h("span", { class: "hearts", "aria-label": `${health} of 3 hearts`, text: hearts(health) }),
    h("span", { class: "muted", text: `Card ${Math.min(i + 1, L.cards.length)} of ${L.cards.length} · ${score} right` }), h("span", { class: "sep" }), timerBtn);
  const frame = gameFrame("approve", n, status, stage);

  function show() {
    cancelAnimationFrame(raf);
    if (i >= L.cards.length || health <= 0) return finish();
    paintHud();
    const [cmd, says] = L.cards[i];
    const ex = explainCommand(cmd);
    const why = h("div", { class: "ad-why", hidden: true }, h("b", { text: ex.summary }),
      ...ex.risks.map(r => h("div", { class: "risk " + r.level, text: (r.level === "danger" ? "Careful: " : "Heads-up: ") + r.text })),
      ex.risks.length ? null : h("p", { class: "muted", text: "No risky pattern spotted. Hover each piece for more." }));
    const bar = h("div", { class: "ad-timer", hidden: !timerOn }, h("i"));
    const choose = c => () => decide(c);
    const askBtn = h("button", { type: "button", class: "btn", onclick: () => { why.hidden = false; askBtn.disabled = true; left = Math.max(1000, left - 3000); } }, "Ask why");
    fill(stage, h("div", { class: "ad-card" }, h("div", { class: "ad-says" }, h("span", { class: "who", text: "AI helper" }), h("span", { text: says })),
      cmdLine(cmd).line, why, bar,
      h("div", { class: "row" }, h("button", { type: "button", class: "btn ad-yes", onclick: choose("approve") }, "Approve"), askBtn,
        h("button", { type: "button", class: "btn ad-no", onclick: choose("deny") }, "Deny"))));
    stage.querySelector(".ad-yes").focus({ preventScroll: true });
    left = L.secs * 1000; last = performance.now();
    const tick = t => {
      const dt = t - last; last = t;
      if (!stage.isConnected) return;  // another level or game was opened
      const paused = !timerOn || document.hidden || $("#view-learn").hidden;
      bar.hidden = !timerOn;
      if (!paused) left -= dt;
      bar.firstChild.style.width = Math.max(0, left / (L.secs * 10)) + "%";
      if (left <= 0) return decide("timeout");
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
  }
  function decide(choice) {
    cancelAnimationFrame(raf);
    const [cmd, , ok, why] = L.cards[i];
    const right = choice === (ok ? "approve" : "deny");
    if (choice === "approve" && !ok) health--;
    if (right) score++;
    log.push({ cmd, choice, ok, right, why });
    const verdict = choice === "timeout" ? (ok ? "Too slow: the AI waited, and that was a fine command." : "Too slow, but no harm: an unanswered request doesn't run.")
      : right ? (ok ? "Right, approved." : "Right, denied.") : (ok ? "Too careful: that one was fine." : "Ouch: you approved it.");
    const next = h("button", { type: "button", class: "btn small primary", onclick: () => { i++; show(); } }, i + 1 >= L.cards.length || health <= 0 ? "See the result" : "Next card");
    fill(stage, h("div", { class: "ad-card " + (right ? "good" : "bad") }, cmdLine(cmd).line,
      h("p", {}, h("b", { text: verdict + " " }), why), h("div", { class: "row" }, next)));
    paintHud();
    next.focus({ preventScroll: true });
  }
  function finish() {
    const won = health > 0 && score >= L.cards.length - 1;
    const list = h("ul", { class: "ad-log" }, ...log.map(e => h("li", { class: e.right ? "ok" : "no" },
      h("span", { class: "mark", text: e.right ? "✓" : "✗" }), h("code", { text: e.cmd }),
      h("small", { text: `${e.choice === "timeout" ? "no answer" : e.choice === "approve" ? "approved" : "denied"} · ${e.why}` }))));
    fill(stage, levelEnd("approve", n, won, won ? `${score} of ${L.cards.length} right with ${health} heart${health === 1 ? "" : "s"} left.`
      : health <= 0 ? "Out of hearts: three dangerous commands ran." : `${score} of ${L.cards.length} right; one mistake at most clears the level.`, list));
    paintHud();
  }
  show();
  return frame;
}

/* ================================================================== 2. Save point rescue */
/* A tiny Git: save points with a snapshot of each file's version, branches, HEAD and unsaved changes.
   Moves refuse what real Git would refuse or what would lose work, and say why. */
function makeRepo(spec) {
  const r = { commits: [], branches: {}, lanes: [], head: spec.head, work: null };
  for (const [id, msg, parents, snap, branch] of spec.commits) addCommit(r, { id, msg, parents: parents ? [].concat(parents) : [], snap, branch });
  Object.assign(r.branches, spec.branches);
  for (const b of Object.keys(spec.branches)) if (!r.lanes.includes(b)) r.lanes.push(b);
  r.work = { ...snapOf(r), ...(spec.work || {}) };
  return r;
}
function addCommit(r, c) {
  if (!r.lanes.includes(c.branch)) r.lanes.push(c.branch);
  r.commits.push(c);
  return c;
}
const rsCommit = (r, id) => r.commits.find(c => c.id === id);
const rsTip = (r, b = r.head) => rsCommit(r, r.branches[b]);
const snapOf = (r, b = r.head) => ({ ...rsTip(r, b).snap });
const dirty = r => Object.keys(r.work).filter(f => r.work[f] !== snapOf(r)[f]);
function ancestors(r, id) { const out = new Set(), todo = [id]; while (todo.length) { const c = rsCommit(r, todo.pop()); if (c && !out.has(c.id)) { out.add(c.id); todo.push(...c.parents); } } return out; }
let saveNo = 0;
const MOVES = {
  save: (r) => {
    const changed = dirty(r);
    if (!changed.length) return "Nothing to save: there are no unsaved changes.";
    const c = addCommit(r, { id: "S" + (++saveNo), msg: "Save: " + changed.join(", "), label: "Saved", parents: [r.branches[r.head]], snap: { ...r.work }, branch: r.head, fresh: true });
    r.branches[r.head] = c.id;
  },
  branch: (r, name) => {
    if (r.branches[name]) return `There is already a branch called ${name}.`;
    r.branches[name] = r.branches[r.head];
    if (!r.lanes.includes(name)) r.lanes.push(name);
    r.head = name;  // unsaved changes come along, as with git switch -c
  },
  switch: (r, name) => {
    if (r.head === name) return `You're already on ${name}.`;
    if (dirty(r).length) return "Git would carry your unsaved changes to the other branch. Save or restore them first, so they stay where they belong.";
    r.head = name;
    r.work = snapOf(r);
  },
  merge: (r, name) => {
    if (dirty(r).length) return "Save or restore your unsaved changes before a merge.";
    if (name === r.head) return "A branch can't be merged into itself.";
    const ours = r.branches[r.head], theirs = r.branches[name], mine = ancestors(r, ours);
    if (mine.has(theirs)) return `${r.head} already has everything from ${name}.`;
    if (ancestors(r, theirs).has(ours)) { r.branches[r.head] = theirs; r.work = snapOf(r); return; }  // fast-forward
    const anc = ancestors(r, theirs), base = [...r.commits].reverse().find(c => mine.has(c.id) && anc.has(c.id));  // the nearest common save point
    const a = rsCommit(r, ours).snap, b = rsCommit(r, theirs).snap, snap = { ...a };
    for (const f of Object.keys(b)) {
      if (b[f] === base.snap[f]) continue;
      if (a[f] !== base.snap[f] && a[f] !== b[f]) return `Both branches changed ${f}: that's a conflict to settle by hand.`;
      snap[f] = b[f];
    }
    const c = addCommit(r, { id: "M" + (++saveNo), msg: `Merge ${name}`, label: "Merge", parents: [ours, theirs], snap, branch: r.head, fresh: true });
    r.branches[r.head] = c.id;
    r.work = { ...snap };
  },
  restore: (r, file) => {
    if (r.work[file] === snapOf(r)[file]) return `${file} has no unsaved changes.`;
    r.work[file] = snapOf(r)[file];
  },
  revert: (r, id) => {
    if (dirty(r).length) return "Save or restore your unsaved changes before undoing a save point.";
    const c = rsCommit(r, id), parent = rsCommit(r, c.parents[0]), now = snapOf(r);
    if (!ancestors(r, r.branches[r.head]).has(id)) return `${id} isn't in ${r.head}'s history.`;
    const undo = Object.keys(c.snap).filter(f => c.snap[f] !== parent.snap[f]);
    for (const f of undo) if (now[f] !== c.snap[f]) return `A later save point changed ${f} too, so it can't be undone cleanly.`;
    const snap = { ...now };
    for (const f of undo) snap[f] = parent.snap[f];
    const n = addCommit(r, { id: "R" + (++saveNo), msg: `Undo ${id}`, label: `Undo ${id}`, parents: [r.branches[r.head]], snap, branch: r.head, fresh: true });
    r.branches[r.head] = n.id;
    r.work = { ...snap };
  },
};
const MOVE_CMD = { save: () => 'git commit -am "save"', branch: a => `git switch -c ${a}`, switch: a => `git switch ${a}`, merge: a => `git merge ${a}`, restore: a => `git restore ${a}`, revert: a => `git revert ${a}` };
const MOVE_LABEL = { save: () => "Save a point", branch: a => `New branch ${a}`, switch: a => `Switch to ${a}`, merge: a => `Merge ${a} in`, restore: a => `Restore ${a}`, revert: a => `Undo save point ${a}` };

GAMES.rescue = { levels: [
  { title: "First save", story: "You finished a change to app.py. Make a save point before you go on.", limit: 1,
    repo: { commits: [["A", "Start", null, { "app.py": "v1" }, "main"]], branches: { main: "A" }, head: "main", work: { "app.py": "v2" } },
    moves: [["save"]], goal: "app.py v2 is saved and nothing is left unsaved.", done: r => !dirty(r).length && snapOf(r)["app.py"] === "v2" },
  { title: "Undo a bad edit", story: "The AI broke weather.py, but your README fix is good. Keep the fix, drop the breakage.", limit: 2,
    repo: { commits: [["A", "Start", null, { "weather.py": "works", "README.md": "old" }, "main"]], branches: { main: "A" }, head: "main", work: { "weather.py": "broken", "README.md": "fixed" } },
    moves: [["restore", "weather.py"], ["restore", "README.md"], ["save"]], goal: "weather.py works, the README fix is saved, nothing unsaved.",
    done: r => !dirty(r).length && snapOf(r)["weather.py"] === "works" && snapOf(r)["README.md"] === "fixed" },
  { title: "Try it on a branch", story: "You have an idea for app.py but main must stay as it is. Put the idea on its own branch.", limit: 2,
    repo: { commits: [["A", "Start", null, { "app.py": "v1" }, "main"]], branches: { main: "A" }, head: "main", work: { "app.py": "idea" } },
    moves: [["save"], ["branch", "idea"]], goal: "Branch idea has the idea saved; main still has app.py v1.",
    done: r => !!r.branches.idea && snapOf(r, "idea")["app.py"] === "idea" && snapOf(r, "main")["app.py"] === "v1" && !dirty(r).length },
  { title: "Bring it home", story: "The idea worked. Get it into main.", limit: 2,
    repo: { commits: [["A", "Start", null, { "app.py": "v1" }, "main"], ["B", "Idea", "A", { "app.py": "idea" }, "idea"]], branches: { main: "A", idea: "B" }, head: "idea" },
    moves: [["merge", "main"], ["switch", "main"], ["merge", "idea"]], goal: "main has the idea.", done: r => snapOf(r, "main")["app.py"] === "idea" },
  { title: "Undo a save point", story: "This morning main.py worked. Then the AI rewrote it (save point B), and later you saved a README fix (C). Get main.py back without losing the fix or the history.", limit: 1,
    repo: { commits: [["A", "Morning", null, { "main.py": "works", "README.md": "old" }, "main"], ["B", "AI rewrite", "A", { "main.py": "broken", "README.md": "old" }, "main"],
      ["C", "README fix", "B", { "main.py": "broken", "README.md": "fixed" }, "main"]], branches: { main: "C" }, head: "main" },
    moves: [["restore", "main.py"], ["revert", "C"], ["revert", "B"]], goal: "main.py works, README is fixed, and A, B and C are still in the history.",
    done: r => snapOf(r)["main.py"] === "works" && snapOf(r)["README.md"] === "fixed" && ["A", "B", "C"].every(id => ancestors(r, r.branches.main).has(id)) },
  { title: "Park your work", story: "A teammate needs main right now, but your change to app.py is half done. Keep it, just not on main.", limit: 3,
    repo: { commits: [["A", "Start", null, { "app.py": "v1" }, "main"]], branches: { main: "A" }, head: "main", work: { "app.py": "half done" } },
    moves: [["restore", "app.py"], ["branch", "wip"], ["save"], ["switch", "main"]], goal: "Your half-done work is saved on wip; you're back on main with app.py v1.",
    done: r => r.head === "main" && !dirty(r).length && snapOf(r, "main")["app.py"] === "v1" && !!r.branches.wip && snapOf(r, "wip")["app.py"] === "half done" },
  { title: "Two fixes, one main", story: "One branch fixes login.py, another fixes upload.py. main needs both.", limit: 2,
    repo: { commits: [["A", "Start", null, { "login.py": "bug", "upload.py": "bug" }, "main"], ["B", "Fix login", "A", { "login.py": "fixed", "upload.py": "bug" }, "fix-login"],
      ["C", "Fix upload", "A", { "login.py": "bug", "upload.py": "fixed" }, "fix-upload"]], branches: { main: "A", "fix-login": "B", "fix-upload": "C" }, head: "main" },
    moves: [["switch", "fix-login"], ["merge", "fix-login"], ["merge", "fix-upload"]], goal: "main has both fixes.",
    done: r => snapOf(r, "main")["login.py"] === "fixed" && snapOf(r, "main")["upload.py"] === "fixed" },
  { title: "The full rescue", story: "Save point B is the AI's broken main.py. Since then you fixed the README (good) and the AI made main.py worse (bad), both unsaved.", limit: 3,
    repo: { commits: [["A", "Morning", null, { "main.py": "works", "README.md": "old" }, "main"], ["B", "AI changes", "A", { "main.py": "broken", "README.md": "old" }, "main"]],
      branches: { main: "B" }, head: "main", work: { "main.py": "worse", "README.md": "fixed" } },
    moves: [["restore", "README.md"], ["revert", "B"], ["save"], ["restore", "main.py"]], goal: "main.py works, the README fix is saved, nothing unsaved, and B stays in the history.",
    done: r => !dirty(r).length && snapOf(r)["main.py"] === "works" && snapOf(r)["README.md"] === "fixed" && ancestors(r, r.branches.main).has("B") },
], play: playRescue };

function playRescue(n) {
  const L = GAMES.rescue.levels[n - 1];
  saveNo = 0;
  const r = makeRepo(L.repo), used = [];
  const pic = h("div", { class: "rs-pic" }), files = h("div", { class: "rs-files" }), cards = h("div", { class: "rs-cards" }), note = h("p", { class: "rs-note", role: "status" });
  const typed = h("input", { type: "text", spellcheck: "false", autocomplete: "off", placeholder: "or type the git command", "aria-label": "Git command",
    onkeydown: e => { if (e.key !== "Enter") return; const v = typed.value.trim().replace(/\s+/g, " ").replace(/'/g, '"'); const m = L.moves.find(([k, a]) => MOVE_CMD[k](a) === v); typed.value = ""; if (m) play(m); else say(`Not one of this level's moves: ${v || "(empty)"}`, true); } });
  const left = h("span", { class: "muted" });
  function say(text, bad) { note.textContent = text; note.className = "rs-note" + (bad ? " bad" : ""); }
  function play([k, a]) {
    if (used.length >= L.limit) return;
    r.commits.forEach(c => { c.fresh = false; });
    const err = MOVES[k](r, a);
    if (err) return say(err, true);
    used.push(MOVE_CMD[k](a));
    say("$ " + MOVE_CMD[k](a));
    paint();
    if (L.done(r)) fill(end, levelEnd("rescue", n, true, `Done in ${used.length} move${used.length === 1 ? "" : "s"}: ${used.join(" → ")}`));
    else if (used.length >= L.limit) fill(end, levelEnd("rescue", n, false, `Out of moves. You tried: ${used.join(" → ")}. Goal: ${L.goal}`));
  }
  function paint() {
    left.textContent = `Moves left: ${L.limit - used.length} of ${L.limit}`;
    pic.replaceChildren(repoSvg(r));
    const saved = snapOf(r), d = dirty(r);
    fill(files, h("table", { class: "dt rs-table" }, h("thead", {}, h("tr", {}, h("th", { text: "File" }), h("th", { text: `Saved on ${r.head}` }), h("th", { text: "Now" }))),
      h("tbody", {}, ...Object.keys(r.work).map(f => h("tr", { class: d.includes(f) ? "dirty" : "" }, h("td", {}, h("code", { text: f })), h("td", { text: saved[f] }),
        h("td", { text: d.includes(f) ? r.work[f] + " (unsaved)" : r.work[f] }))))));
    fill(cards, ...L.moves.map(m => h("button", { type: "button", class: "rs-card", disabled: used.length >= L.limit, onclick: () => play(m) },
      h("b", { text: MOVE_LABEL[m[0]](m[1]) }), h("code", { text: MOVE_CMD[m[0]](m[1]) }))));
  }
  const end = h("div");
  const frame = gameFrame("rescue", n, h("div", { class: "rs-goal" }, h("b", { text: "Goal: " }), L.goal, " ", left), pic, files, cards,
    h("div", { class: "row" }, typed, h("button", { type: "button", class: "btn small", onclick: () => openGame("rescue", n) }, "Start over")), note, end);
  paint();
  return frame;
}
/* History as a picture: one lane per branch, save points left to right, branch names at their tips, HEAD in bold,
   unsaved changes as a dashed point after HEAD. */
function repoSvg(r) {
  const NS = "http://www.w3.org/2000/svg", dx = 76, dy = 46, x0 = 30, y0 = 26;
  const pos = new Map(r.commits.map((c, i) => [c.id, [x0 + i * dx, y0 + r.lanes.indexOf(c.branch) * dy]]));
  const d = dirty(r).length, w = x0 + (r.commits.length + (d ? 1 : 0)) * dx + 120, hgt = y0 + r.lanes.length * dy;
  const el = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
  const svg = el("svg", { viewBox: `0 0 ${w} ${hgt}`, width: w, height: hgt, role: "img", "aria-label": `History: ${r.commits.map(c => c.id + " " + c.msg).join(", ")}; on ${r.head}` });
  for (const c of r.commits) for (const p of c.parents) {
    const [x1, y1] = pos.get(p), [x2, y2] = pos.get(c.id);
    svg.append(el("path", { d: y1 === y2 ? `M${x1} ${y1}H${x2}` : `M${x1} ${y1}C${x1 + dx / 2} ${y1} ${x2 - dx / 2} ${y2} ${x2} ${y2}`, class: "rs-edge" }));
  }
  const [hx, hy] = pos.get(r.branches[r.head]);
  if (d) {
    const ux = x0 + r.commits.length * dx;
    svg.append(el("path", { d: `M${hx} ${hy}C${hx + dx / 2} ${hy} ${ux - dx / 2} ${hy} ${ux} ${hy}`, class: "rs-edge dashed" }), el("circle", { cx: ux, cy: hy, r: 9, class: "rs-dirty" }),
      el("text", { x: ux, y: hy + 23, class: "rs-small", "text-anchor": "middle" }, "unsaved"));
  }
  for (const c of r.commits) {
    const [x, y] = pos.get(c.id);
    const g = el("g", { class: "rs-node" + (c.fresh ? " fresh" : "") });
    g.append(el("title", {}, `${c.id}: ${c.msg}`), el("circle", { cx: x, cy: y, r: 11 }), el("text", { x, y: y + 4, "text-anchor": "middle" }, c.id),
      el("text", { x, y: y + 23, class: "rs-msg", "text-anchor": "middle" }, (c.label || c.msg).length > 11 ? (c.label || c.msg).slice(0, 10) + "…" : c.label || c.msg));
    svg.append(g);
  }
  const tips = {};
  for (const [b, id] of Object.entries(r.branches)) (tips[id] = tips[id] || []).push(b);
  for (const [id, names] of Object.entries(tips)) {
    const [x, y] = pos.get(id);
    names.forEach((b, k) => svg.append(el("text", { x: x + 15, y: y - 13 - k * 13, class: "rs-branch" + (b === r.head ? " head" : "") }, b === r.head ? `${b} ← you are here` : b)));
  }
  return svg;
}

/* ================================================================== 3. Treasure hunt */
/* A pretend folder tree in memory. Keys ending in "/" are folders; the rest are files with their text. */
GAMES.hunt = { levels: [
  { title: "Look around", story: "Somewhere in your home folder is a treasure note. Start with ls to see what's here.", target: "box/treasure.txt",
    hints: ["Type ls and press Enter to list what's here.", "There's a folder called box. Walk in with cd box.", "Inside box, show the note with cat treasure.txt."],
    fs: { "notes.txt": "Note to self: I put the treasure in the box.", "box/": { "treasure.txt": "★ You found it: the first treasure! ★", "lid.txt": "Just a lid." }, "photos/": { "cat.jpg": "(a picture of a cat)" } } },
  { title: "Hidden things", story: "This treasure hides. Names that start with a dot are hidden from a plain ls.", target: ".cave/treasure.txt",
    hints: ["Plain ls hides names that start with a dot. Try ls -a.", "There's a hidden folder: cd .cave", "cat treasure.txt"],
    fs: { "readme.txt": "Nothing to see here. Or is there?", ".cave/": { "treasure.txt": "★ Hidden treasure found! ★", ".bats": "squeak" }, "garden/": { "rose.txt": "A rose." } } },
  { title: "Search the text", story: "Twelve log files, and one of them says where the treasure is. Reading them one by one takes ages: search instead.", target: "attic/chest/treasure.txt",
    hints: ["grep searches inside files. -r means: in this folder and every folder below.", "Try grep -r treasure logs", "The matching line names the place. cat that file."],
    fs: { "logs/": Object.fromEntries([...Array(12)].map((_, i) => [`day${String(i + 1).padStart(2, "0")}.log`, i === 8 ? "09:12 moved the treasure to attic/chest/treasure.txt\n09:13 lunch" : `08:0${i % 10} coffee\n12:00 lunch\n17:30 home`])),
      "attic/": { "chest/": { "treasure.txt": "★ Found by searching, not by reading every file! ★" }, "dust.txt": "dust" } } },
  { title: "Find by name", story: "The treasure is a file called crystal.key, buried deep in a maze of folders.", target: "maze/left/up/right/down/crystal.key",
    hints: ["find looks for files by name: find . -name crystal.key", "Then cat the path it printed.", "Paths can be long: Tab completes names as you type."],
    fs: { "maze/": { "left/": { "up/": { "right/": { "down/": { "crystal.key": "★ The crystal key is yours! ★" }, "wall.txt": "A wall." } }, "dead-end.txt": "Dead end." }, "right/": { "dead-end.txt": "Dead end." }, "straight/": { "ahead/": { "nothing.txt": "Nothing." } } } } },
  { title: "Long scroll", story: "map.txt is three hundred lines long, but its first lines say where to dig.", target: "beach/dig-here/treasure.txt",
    hints: ["head shows the first lines of a file: head map.txt", "head -n 3 map.txt shows just three.", "Follow the directions in those lines."],
    fs: { "map.txt": ["Directions: walk to beach, then dig-here.", "The treasure file is called treasure.txt.", "Everything below is sand."].concat([...Array(297)].map((_, i) => `sand sand sand ${i}`)).join("\n"),
      "beach/": { "dig-here/": { "treasure.txt": "★ X marks the spot! ★" }, "shells.txt": "Shells." }, "forest/": { "tree.txt": "A tree." } } },
  { title: "Put it together", story: "A secret word is hidden somewhere, written in capitals or not. Then a hidden folder, then a file found by name.", target: ".vault/deep/gold.txt",
    hints: ["grep -ri ignores capitals: grep -ri secret .", "The clue mentions a hidden folder: ls -a", "find .vault -name gold.txt, then cat it."],
    fs: { "docs/": { "a.txt": "plans", "b.txt": "The SECRET: look in the hidden vault, for gold.", "c.txt": "more plans" }, ".vault/": { "deep/": { "gold.txt": "★ Gold! You used grep, ls -a and find. ★" }, "lock.txt": "locked" }, "old/": { "junk.txt": "junk" } } },
], play: playHunt };

function buildFs(spec) {
  const dir = { d: true, kids: {} };
  for (const [k, v] of Object.entries(spec)) dir.kids[k.replace(/\/$/, "")] = k.endsWith("/") ? buildFs(v) : { d: false, text: v };
  return dir;
}
function playHunt(n) {
  const L = GAMES.hunt.levels[n - 1], root = buildFs(L.fs), history = [];
  let cwd = [], hintNo = 0, hist = -1, won = false;
  const out = h("div", { class: "th-out", role: "log", "aria-live": "polite" });
  const promptText = () => `you@hunt:${cwd.length ? "~/" + cwd.join("/") : "~"}$`;
  const prompt = h("span", { class: "th-prompt", text: promptText() });
  const input = h("input", { type: "text", class: "th-in", spellcheck: "false", autocomplete: "off", autocapitalize: "off", "aria-label": "Command", onkeydown: keys });
  const end = h("div");
  const print = (text, cls) => { out.append(h("div", { class: cls || "", text })); out.scrollTop = out.scrollHeight; };
  const resolve = (p) => {
    const parts = !p || p === "~" ? [] : p.startsWith("~/") ? p.slice(2).split("/") : p.startsWith("/") ? p.slice(1).split("/") : [...cwd, ...p.split("/")];
    const stack = [];
    for (const x of parts) { if (!x || x === ".") continue; if (x === "..") stack.pop(); else stack.push(x); }
    let node = root;
    for (const x of stack) { node = node.d ? node.kids[x] : null; if (!node) return null; }
    return { node, path: stack };
  };
  const walk = (node, path, fn) => { for (const [k, v] of Object.entries(node.kids)) { fn(v, [...path, k]); if (v.d) walk(v, [...path, k], fn); } };
  const rel = path => path.slice(cwd.length).join("/") || ".";
  const glob = pat => new RegExp("^" + pat.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
  function showFile(node, path, lines) {
    const text = lines ? node.text.split("\n").slice(0, lines).join("\n") : node.text;
    print(text);
    if (path.join("/") === L.target && !won) { won = true; input.disabled = true; fill(end, levelEnd("hunt", n, true, `Found with: ${history.join(" · ")}`)); }
  }
  const CMDS = {
    help: () => print("Commands: pwd  ls [-a] [folder]  cd folder  cat file  head [-n N] file  grep [-r] [-i] word [where]  find [where] -name name  clear  hint"),
    hint: () => { print("Hint: " + L.hints[Math.min(hintNo, L.hints.length - 1)], "th-hint"); hintNo++; },
    clear: () => out.replaceChildren(),
    pwd: () => print(cwd.length ? "/home/you/" + cwd.join("/") : "/home/you"),
    ls: args => {
      const all = args.some(a => /^-\w*a/.test(a)), target = args.find(a => !a.startsWith("-"));
      const r = resolve(target);
      if (!r) return print(`ls: ${target}: no such file or folder`, "th-err");
      if (!r.node.d) return print(target);
      const names = Object.keys(r.node.kids).filter(k => all || !k.startsWith(".")).sort().map(k => r.node.kids[k].d ? k + "/" : k);
      print((all ? ["./", "../", ...names] : names).join("  ") || "(empty)");
    },
    cd: args => {
      const r = resolve(args[0]);
      if (!r) return print(`cd: ${args[0]}: no such folder`, "th-err");
      if (!r.node.d) return print(`cd: ${args[0]}: not a folder`, "th-err");
      cwd = r.path; prompt.textContent = promptText();
    },
    cat: args => {
      if (!args.length) return print("cat: which file? For example: cat notes.txt", "th-err");
      for (const a of args) { const r = resolve(a); if (!r) print(`cat: ${a}: no such file`, "th-err"); else if (r.node.d) print(`cat: ${a}: is a folder (try ls ${a})`, "th-err"); else showFile(r.node, r.path); }
    },
    head: args => {
      let lines = 10; const files = [];
      for (let i = 0; i < args.length; i++) { if (args[i] === "-n") lines = parseInt(args[++i], 10) || 10; else if (/^-\d+$/.test(args[i])) lines = -parseInt(args[i], 10); else files.push(args[i]); }
      if (!files.length) return print("head: which file?", "th-err");
      const r = resolve(files[0]);
      if (!r || r.node.d) return print(`head: ${files[0]}: no such file`, "th-err");
      showFile(r.node, r.path, lines);
    },
    grep: args => {
      const flags = args.filter(a => a.startsWith("-")).join(""), rest = args.filter(a => !a.startsWith("-"));
      const word = (rest[0] || "").replace(/^["']|["']$/g, ""), where = rest.slice(1);
      if (!word) return print("grep: what word? For example: grep -r treasure .", "th-err");
      const recurse = /r/i.test(flags), ci = /i/.test(flags), hits = [];
      const match = line => ci ? line.toLowerCase().includes(word.toLowerCase()) : line.includes(word);
      for (const w of where.length ? where : ["."]) {
        const r = resolve(w);
        if (!r) { print(`grep: ${w}: no such file or folder`, "th-err"); continue; }
        const scan = (node, path) => node.text.split("\n").forEach(line => { if (match(line)) hits.push(`${rel(path)}: ${line}`); });
        if (!r.node.d) scan(r.node, r.path);
        else if (!recurse) print(`grep: ${w}: is a folder (add -r to search inside it)`, "th-err");
        else walk(r.node, r.path, (v, p) => { if (!v.d) scan(v, p); });
      }
      print(hits.length ? hits.join("\n") : "(no lines matched)");
    },
    find: args => {
      const i = args.indexOf("-name"), where = i > 0 ? args[0] : args[0] && args[0] !== "-name" ? args[0] : ".", pat = i >= 0 ? (args[i + 1] || "").replace(/^["']|["']$/g, "") : null;
      const r = resolve(where);
      if (!r || !r.node.d) return print(`find: ${where}: no such folder`, "th-err");
      const re = pat ? glob(pat) : null, found = [];
      walk(r.node, r.path, (v, p) => { if (!re || re.test(p[p.length - 1])) found.push(rel(p)); });
      print(found.length ? found.join("\n") : "(nothing found)");
    },
  };
  function run(line) {
    const words = line.match(/"[^"]*"|'[^']*'|\S+/g) || [];
    print(`${promptText()} ${line}`, "th-echo");
    if (!words.length) return;
    history.push(line);
    const f = CMDS[words[0]];
    if (f) f(words.slice(1));
    else print(`${words[0]}: not a command here. Type help for the list.`, "th-err");
  }
  function complete() {
    const words = input.value.split(" "), last = words[words.length - 1], slash = last.lastIndexOf("/");
    const base = resolve(slash >= 0 ? last.slice(0, slash) || "/" : "."), stem = last.slice(slash + 1);
    if (!base || !base.node.d) return;
    const opts = Object.keys(base.node.kids).filter(k => k.startsWith(stem) && (stem.startsWith(".") || !k.startsWith(".")));
    if (opts.length === 1) { words[words.length - 1] = last.slice(0, slash + 1) + opts[0] + (base.node.kids[opts[0]].d ? "/" : ""); input.value = words.join(" "); }
    else if (opts.length > 1) print(opts.join("  "), "th-hint");
  }
  function keys(e) {
    if (e.key === "Enter") { const v = input.value.trim(); input.value = ""; hist = -1; run(v); }
    else if (e.key === "Tab") { e.preventDefault(); complete(); }
    else if (e.key === "ArrowUp" && history.length) { e.preventDefault(); hist = hist < 0 ? history.length - 1 : Math.max(0, hist - 1); input.value = history[hist]; }
    else if (e.key === "ArrowDown" && hist >= 0) { e.preventDefault(); hist = hist + 1 < history.length ? hist + 1 : -1; input.value = hist < 0 ? "" : history[hist]; }
  }
  const frame = gameFrame("hunt", n, h("div", { class: "th-term", onclick: () => input.focus() }, out, h("label", { class: "th-line" }, prompt, input)),
    h("div", { class: "row" }, h("button", { type: "button", class: "btn small", onclick: () => CMDS.hint() }, "Hint"),
      h("button", { type: "button", class: "btn small", onclick: () => { const last = history[history.length - 1]; if (last) { $("#dec-in").value = last; decode(); showSub("decoder"); } } }, "Explain my last command"),
      h("span", { class: "muted", text: "Pretend files: nothing here touches your computer." })), end);
  print("Type help for the commands, or press Hint.", "th-hint");
  return frame;
}

/* ================================================================== 4. Token budget */
/* Tiers with made-up prices; a task names the smallest tier that does it right and whether it holds private data. */
const TIERS = [
  ["local", "Local model", 0, "Free and private, on your computer. Small, simple edits."],
  ["small", "Small cloud model", 0.01, "Cheap and fast. Routine text and code."],
  ["medium", "Medium cloud model", 0.05, "Solid at real code: tests, bug fixes."],
  ["large", "Large cloud model", 0.40, "The deep thinker: design and tricky bugs."],
];
const dollars = v => "$" + v.toFixed(2);
GAMES.budget = { levels: [
  { title: "Small jobs", budget: 0.06, tasks: [
    ["Rename a variable in one file", 0, false, "A local model renames reliably and costs nothing."],
    ["Write a commit message for a small diff", 0, false, "Short and simple: local is plenty."],
    ["Explain an error message in plain words", 1, false, "A small cloud model explains well; local ones often guess."],
    ["Translate a README paragraph into Spanish", 1, false, "Routine text: small is right."]] },
  { title: "A real feature", budget: 0.22, tasks: [
    ["Write unit tests for a date parser", 2, false, "Good tests need real code sense: medium."],
    ["Fix a bug from a stack trace", 2, false, "Reading the trace and the code together: medium."],
    ["Add a docstring to one function", 0, false, "Simple: local."],
    ["Summarise a pull request for a reviewer", 1, false, "Routine text: small."]] },
  { title: "Secrets stay home", budget: 0.12, tasks: [
    ["Tidy an .env file that holds API keys", 0, true, "Private data: only a local model may see it."],
    ["Rename the settings that hold passwords in config.py", 0, true, "The passwords are in the file: local only."],
    ["Explain what a regular expression matches", 1, false, "No secrets here, and small explains it well."],
    ["Write tests for a function that hashes passwords", 2, false, "The code has no secrets in it; medium writes good tests."]] },
  { title: "Hard problems", budget: 0.95, tasks: [
    ["Design the database for an online shop", 3, false, "Design work pays off with the large model."],
    ["Find a race condition in a threaded uploader", 3, false, "Subtle concurrency bugs need the deep thinker."],
    ["Format a JSON file", 0, false, "A formatter or a local model: free."],
    ["Write a changelog entry from three commits", 1, false, "Routine text: small."]] },
  { title: "A day of work", budget: 0.72, tasks: [
    ["Rename a function across the project", 0, false, "Mechanical: local."],
    ["Draft an email to a client about a delay", 1, false, "Routine text: small."],
    ["Plan the architecture of a new sync feature", 3, false, "Big design decisions: large."],
    ["Fix a failing test from its error output", 2, false, "Code and error together: medium."],
    ["Strip the API keys from a log before sharing it", 0, true, "The keys must never leave your computer: local."],
    ["Turn a bulleted list into a table", 0, false, "Simple: local."],
    ["Review a pull request for security problems", 2, false, "Careful code reading: medium."],
    ["Write the release notes", 1, false, "Routine text: small."]] },
], play: playBudget };

function playBudget(n) {
  const L = GAMES.budget.levels[n - 1], log = [];
  let i = 0, spent = 0, leaks = 0;
  const best = L.tasks.reduce((s, t) => s + TIERS[t[1]][2], 0);
  const meter = h("div", { class: "tb-meter" }), stage = h("div", { class: "tb-stage" }), end = h("div");
  const paint = () => fill(meter, h("span", { text: `Task ${Math.min(i + 1, L.tasks.length)} of ${L.tasks.length}` }),
    h("div", { class: "tb-bar" + (spent > L.budget ? " over" : "") }, h("i", { style: `width:${Math.min(100, (spent / L.budget) * 100)}%` })),
    h("span", { class: spent > L.budget ? "bad" : "muted", text: `Spent ${dollars(spent)} of ${dollars(L.budget)}` }));
  function show(msg) {
    paint();
    if (i >= L.tasks.length) return finish();
    const [text, , priv] = L.tasks[i];
    fill(stage, h("div", { class: "tb-task" }, h("b", { text }), priv ? h("span", { class: "chip warn", text: "holds private data" }) : null),
      msg || null,
      h("div", { class: "tb-tiers" }, ...TIERS.map(([k, label, price, what], t) => h("button", { type: "button", class: "tb-tier", onclick: () => pick(t) },
        h("b", { text: label }), h("span", { class: "price", text: price ? dollars(price) : "free" }), h("small", { text: what })))));
  }
  function pick(t) {
    const [text, need, priv, why] = L.tasks[i], price = TIERS[t][2];
    spent += price;
    if (priv && t > 0) {
      leaks++; log.push({ text, t, ok: false }); i++;
      return show(h("p", { class: "tb-msg bad" }, h("b", { text: "Leaked: " }), `private data went to a cloud model. ${why}`));
    }
    if (t < need) return show(h("p", { class: "tb-msg bad" }, h("b", { text: "Too small: " }), `the answer was wrong, and the ${dollars(price)} is spent anyway. Pick again. ${why}`));
    log.push({ text, t, ok: true, extra: price - TIERS[need][2] }); i++;
    show(h("p", { class: "tb-msg " + (t === need ? "good" : "meh") }, h("b", { text: t === need ? "Just right. " : `Works, but ${dollars(price - TIERS[need][2])} more than needed. ` }), why));
  }
  function finish() {
    const won = !leaks && spent <= L.budget + 1e-9;
    fill(stage, levelEnd("budget", n, won, won ? `All done for ${dollars(spent)}; the cheapest possible was ${dollars(best)}.`
      : leaks ? "Private data left the computer: that can't be undone, so the level doesn't count." : `Over budget: ${dollars(spent)} of ${dollars(L.budget)}. The cheapest possible was ${dollars(best)}.`,
    h("ul", { class: "ad-log" }, ...log.map(e => h("li", { class: e.ok ? "ok" : "no" }, h("span", { class: "mark", text: e.ok ? "✓" : "✗" }), h("span", { text: e.text }),
      h("small", { text: TIERS[e.t][1] + (e.ok && e.extra > 0 ? ` (+${dollars(e.extra)})` : "") })))),
    h("p", { class: "muted", text: "Prices are made up for the game; real ones differ by provider and change often. The pattern holds: spend on the hard tenth." })));
  }
  const frame = gameFrame("budget", n, meter, stage, end);
  show();
  return frame;
}

/* The view bar was drawn before this file loaded: redraw it so the Learn tab gets its flyout (no hover tip). */
if ($("#nav")) applyLayout((PREFS || DEFAULT_PREFS).layout);
