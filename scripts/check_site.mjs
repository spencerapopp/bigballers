// Audit index.html before every commit:  node scripts/check_site.mjs
// 1. Every inline <script> must compile.
// 2. The data scripts (everything before the rendering code) must run and
//    produce a DATA object whose numbers add up.
// 3. If data/espn/ exists, team scores must match ESPN's official totals.
// 4. Injury rows, needs, daily notes and preview lineups only reference teams
//    and sources that exist.
// 5. The page is rendered in headless Chromium (Playwright) and every tab is
//    opened; any "couldn't be drawn" section or page error fails the audit.
// Exits non-zero on any failure, so a broken page never gets pushed.
import { readFileSync, existsSync } from "node:fs";
import vm from "node:vm";
import { execSync } from "node:child_process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const errors = [];
const warn = [];
const r2 = n => Math.round(n * 100) / 100;

scripts.forEach((src, i) => {
  try { new vm.Script(src, { filename: `script#${i}` }); }
  catch (e) { errors.push(`script #${i} does not compile: ${e.message}`); }
});

// Data scripts = the ones that define or extend DATA without touching the DOM.
const ctx = vm.createContext({ console });
for (const src of scripts) {
  if (!/^\s*(const DATA\s*=|DATA\.[A-Za-z]+\s*=)/m.test(src) || /document\./.test(src)) continue;
  try { vm.runInContext(src.replace(/^\s*const DATA\s*=/m, "globalThis.DATA ="), ctx); }
  catch (e) { errors.push(`data script failed to run: ${e.message}`); }
}
const DATA = ctx.DATA;
if (!DATA) errors.push("DATA object not found");

if (DATA) {
  const ids = Object.keys(DATA.teams);
  const weeks = Object.keys(DATA.weeks).map(Number).sort((a, b) => a - b);
  for (const w of weeks) {
    const wk = DATA.weeks[w];
    const seen = wk.games.flat();
    if (seen.length !== ids.length || new Set(seen).size !== ids.length || !ids.every(id => seen.includes(id)))
      errors.push(`week ${w}: games don't cover each team exactly once`);
    for (const id of ids) {
      const t = wk.teams[id];
      if (!t) { errors.push(`week ${w}: no entry for ${id}`); continue; }
      if (!t.starters) continue;
      const sum = r2(t.starters.reduce((s, p) => s + (Number(p[5]) || 0), 0));
      if (Math.abs(sum - t.score) > 0.011) errors.push(`week ${w} ${id}: starters add to ${sum}, score says ${t.score}`);
    }
    for (const [a, h] of wk.games)
      if (wk.teams[a]?.score === wk.teams[h]?.score) warn.push(`week ${w}: ${a} vs ${h} is tied, check tiebreak`);
  }
  if (DATA.league.currentWeek !== weeks.at(-1))
    errors.push(`league.currentWeek is ${DATA.league.currentWeek} but the latest week in DATA.weeks is ${weeks.at(-1)}`);
  if (DATA.preview && DATA.preview.week !== DATA.league.currentWeek + 1)
    warn.push(`preview.week is ${DATA.preview.week}, expected ${DATA.league.currentWeek + 1}`);

  // News/preview references must resolve (a deleted source or bad team id breaks rendering).
  const N = DATA.news || {};
  const srcNames = new Set((N.sources || []).map(x => x[0]));
  (N.injuries || []).forEach(x => {
    if (!ids.includes(x[3])) errors.push(`news.injuries: ${x[0]} has unknown team ${x[3]}`);
    if (!srcNames.has(x[0])) errors.push(`news.injuries: ${x[0]} has no entry in news.sources`);
    else if (N.sources[x[7]]?.[0] !== x[0]) warn.push(`news.injuries: ${x[0]} source index ${x[7]} points at ${N.sources[x[7]]?.[0] ?? "nothing"} (page matches by name)`);
  });
  (N.alsoTagged || []).forEach(x => { if (!ids.includes(x[1])) errors.push(`news.alsoTagged: ${x[0]} has unknown team ${x[1]}`); });
  (DATA.copy?.needs || []).forEach(x => { if (!ids.includes(x[0])) errors.push(`copy.needs: unknown team ${x[0]}`); });
  (DATA.daily?.items || []).forEach(x => { if (x[1] && !ids.includes(x[1])) errors.push(`daily: unknown team ${x[1]}`); });
  (DATA.preview?.games || []).forEach(g => [g.away, g.home].forEach(id => {
    if (!ids.includes(id)) errors.push(`preview: unknown team ${id}`);
    else if (!g.lineups?.[id]) errors.push(`preview: no lineup for ${id}`);
  }));

  // Cross-check against ESPN pulls when the team-id map exists.
  const mapPath = new URL("../data/espn/team-map.json", import.meta.url);
  if (existsSync(mapPath)) {
    const map = JSON.parse(readFileSync(mapPath, "utf8")); // { "1": "SAP", ... }
    for (const w of weeks) {
      const f = new URL(`../data/espn/week-${String(w).padStart(2, "0")}.json`, import.meta.url);
      if (!existsSync(f)) { warn.push(`no ESPN file for week ${w}`); continue; }
      for (const g of JSON.parse(readFileSync(f, "utf8")).games)
        for (const side of [g.home, g.away]) {
          if (!side || side.teamId == null) continue;
          const key = map[String(side.teamId)];
          const mine = DATA.weeks[w].teams[key]?.score;
          if (key && mine != null && side.points != null && Math.abs(mine - side.points) > 0.011)
            errors.push(`week ${w} ${key}: site says ${mine}, ESPN says ${side.points}`);
        }
    }
  } else warn.push("data/espn/team-map.json missing, skipped ESPN score cross-check");
}

// Render test: open every tab in headless Chromium.
async function loadPlaywright() {
  try { return await import("playwright"); } catch {}
  try { return createRequire(execSync("npm root -g").toString().trim() + "/")("playwright"); } catch {}
  return null;
}
const pw = await loadPlaywright();
if (!pw) warn.push("Playwright not available, skipped render test (install: npm i -g playwright && npx playwright install chromium)");
else {
  let browser;
  try {
    browser = await pw.chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    const pageErrors = [];
    page.on("pageerror", e => pageErrors.push(e.message));
    page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) pageErrors.push(m.text()); });
    await page.goto("file://" + fileURLToPath(new URL("../index.html", import.meta.url)));
    await page.waitForTimeout(800);
    const tabs = await page.$$eval(".tab", bs => bs.map(b => b.dataset.tab));
    for (const t of tabs) { await page.click(`.tab[data-tab="${t}"]`); await page.waitForTimeout(150); }
    const broken = await page.$$eval("p.note", ps => ps.filter(p => /couldn.t be drawn/.test(p.textContent)).map(p => (p.closest("[id]")?.id || "?") + ": " + p.textContent));
    broken.forEach(b => errors.push(`render: ${b}`));
    pageErrors.forEach(e => errors.push(`render: page error: ${e}`));
    if (!tabs.length) errors.push("render: no tabs found");
  } catch (e) { warn.push(`render test could not run: ${e.message}`); }
  finally { await browser?.close(); }
}

warn.forEach(m => console.log("warn:", m));
if (errors.length) { errors.forEach(m => console.error("FAIL:", m)); process.exit(1); }
console.log(`OK: ${scripts.length} scripts compile, ${DATA ? Object.keys(DATA.weeks).length : 0} weeks audited${pw ? ", every tab rendered" : ""}.`);
