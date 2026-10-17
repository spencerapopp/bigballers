# Big Ballers dashboard runbook

Instructions for the scheduled Claude runs that keep https://spencerapopp.github.io/bigballers/ current. The site is one file, `index.html`, served by GitHub Pages from `main`. Spencer Popp (team "Saquon my Dak", ESPN teamId 1) is the league manager and owner of this repo.

## Where things live

| What | Where |
|---|---|
| League | ESPN "BIG BALLERS 2026-2027", leagueId 432801, season 2026. Half-PPR, H2H points, 12 teams, 14-week regular season, 6 playoff teams, tiebreaker total points for. |
| League data (source of truth for numbers) | `data/espn/*.json`, written by the **ESPN data refresh** GitHub Action (`.github/workflows/espn-data.yml`, `scripts/fetch_espn.py`) at 11:15 and 22:15 UTC daily. |
| Site data | The `const DATA = {...}` script block in `index.html`, plus `DATA.copy = {...}` (headline, storylines, grades, power blurbs, preview copy) and `DATA.news = {...}` (injuries, sources, experts). Every section on the page renders from these. |
| Rendering code | The last `<script>` block. Don't touch it unless a section is visibly broken. |
| Audit | `node scripts/check_site.mjs` must pass before any commit. |
| Archive | Google Drive: `Big Ballers League / 2026 Dashboard / Week N / index.html` (2026 Dashboard folder id `1OX2K5GVAhdqjknCWx0XGK_mWEaHeM3uZ`). |

### data/espn files

- `league.json`: teams (records, PF/PA, streak, waiver rank, move counts, current rosters with injury status and lineup slot), full schedule with scores, current scoring/matchup period.
- `week-NN.json`: box score per matchup for week NN. Every rostered player with lineup slot, actual points, ESPN projection, injury status.
- `transactions-NN.json`: executed adds, drops, waiver claims and trades in week NN (lineup shuffles excluded).
- `free-agents.json`: top 60 available players by ownership, with injury tags.
- `nfl-schedule.json`: every NFL team's bye and kickoff times.
- `team-map.json`: ESPN teamId → dashboard key (`SAP`, `LHC`, ...). If a team renames, update `DATA.teams` but never re-key.

Player rows in `DATA.weeks[w].teams[key].starters/bench` are `[slot, name, NFL team, pos, ESPN projection, actual points]`. Build them from `week-NN.json` with a script, never by hand-copying numbers.

## Before every run

1. Clone `spencerapopp/bigballers` and `git pull`.
2. Check data freshness: `gh api repos/spencerapopp/bigballers/actions/workflows/espn-data.yml/runs?per_page=1`. If the latest run is older than 3 hours or failed, dispatch it (`gh api -X POST repos/spencerapopp/bigballers/actions/workflows/espn-data.yml/dispatches -f ref=main -f 'inputs[weeks]=recent'`), poll every 30s for up to 6 minutes, then `git pull`.
3. If the latest run **failed** at the "Fetch league data" step (check `gh api repos/spencerapopp/bigballers/actions/runs/<id>/jobs`), read the reason from the job's annotations: `gh api repos/spencerapopp/bigballers/check-runs/<job id>/annotations` (title "ESPN fetch failed"). Run logs aren't readable from Claude sessions. "Secrets are missing" or "HTTP 401/403" means the ESPN cookies need adding or refreshing. Dispatch once more; if it fails again, stop. Don't edit the site. Tell Spencer: "ESPN data pull is failing, most likely the login expired. On fantasy.espn.com in Chrome, copy the espn_s2 and SWID cookies into the repo's Actions secrets ESPN_S2 and ESPN_SWID."
4. Compare what's on the site with the ESPN files to decide what changed.

## Weekly full update (Tuesday morning, after Monday Night Football)

The week just finished is W = `league.json` → the last matchup period whose games all have final scores. Next week is W+1.

1. Add `DATA.weeks[W]` from `week-W.json`: games (same away/home order ESPN uses), each team's score, projection, starters and bench, `full: true`. Add the kickoff `windows` block like prior weeks, using `nfl-schedule.json`.
2. Re-pull weeks W-1 and W-2 and apply any ESPN stat corrections to scores and player rows.
3. Update `league.currentWeek = W` and `league.updated` (e.g. "Tuesday, Oct 20, 2026").
4. Rewrite `DATA.copy` for the week: kicker/headline/deck, storylines, recaps[W], team grades and report titles for all 12 teams, power blurbs, loss/game of the week, needs, receipts, Thursday-night note, preview copy for W+1.
5. Rebuild `DATA.preview` for W+1: matchups with ESPN projections and win odds, waiver order, best available (`free-agents.json`), upcoming byes, bench notes, `asOf`.
6. Refresh `DATA.news`: current injury tags for every rostered player who matters (starter or flex-worthy), with a source URL per player (ESPN player pages, team reports) and current expert links (ESPN rankings, waiver pickups, winners/losers). Use web search for this. Paraphrase; no quotes over a dozen words.
7. History/record book: update any season or all-time records broken this week.
8. Audit (below), commit `Week W full update`, push, archive to Drive as `2026 Dashboard/Week W/index.html` (create the folder).

The overview is a league-wide week in review. Matchups, comebacks and storylines get their own sections. Every team gets a real paragraph of analysis. Include the true win/loss matrix (each team vs. every other team's score each week) and keep it consistent with the scores.

## Quick update (Wednesday–Monday, about 7:45 AM and 5:45 PM Central)

Goal: keep the page true between weekly updates. Small and factual.

Make an update **only** if at least one of these happened since the last commit to `index.html`:

- An executed add, drop, waiver claim or trade (`transactions-*.json`).
- An injury designation changed for a player in a lineup or on the bench (Q/D/O/IR/active), or credible news that changes whether a starter plays (ruled out, placed on IR, activated, suspension).
- A stat correction changed a final score or result.
- Kickoff-window news for this week's matchups that changes what the preview says (e.g. a projected starter now on bye or out with no backup).

If none happened: don't edit, don't commit. End the run with a one-line note. A quick update with nothing to report is a success.

When there is something:

- Edit only `DATA.news` (injuries, alsoTagged, sources, asOf), `DATA.preview` (lineup notes, needs, bench notes, asOf), the Thursday note, a team's `needs` line, and `league.updated`. Add a dated line to the relevant team's preview note if it matters for the matchup.
- Don't rewrite the headline, grades, recaps or power rankings. Those belong to Tuesday.
- Commit message names the facts: `Quick update: Darren claims Matt Gay, drops Mevis; Lamb out (quad)`.
- During live games (Thu night, Sun, Mon night) don't publish live scores; final results wait for Tuesday.

## Voice

- Edgy, rude, borderline offensive. It's a fun league among friends. Roast decisions with numbers: a manager benching 30 points, starting a player on bye, chasing last week's waiver hero.
- Real stats only. Every number must come from `data/espn` or a cited source. No invented quotes, no made-up projections, no fake trends.
- No filler. Ban: "in a stunning turn of events", "it's worth noting", "let's dive in", "only time will tell", "a testament to", "at the end of the day", "buckle up", exclamation-mark hype, emoji, and paragraphs that restate the table above them. Lead with the fact, then the joke.
- Name managers by first name or last name the way the current copy does (Spencer, Kyle, Bagwell, Kramer, Verber, Rohan...).
- Keep the design: blue scheme built on `#5F93DB`. No green or yellow accents.

## Audit (every run that edits)

1. `node scripts/check_site.mjs` passes: scripts compile, each team's starters sum to its score, every week covers all 12 teams once, scores match ESPN.
2. Records, PF/PA and standings on the page match `league.json` exactly.
3. Every injury line has a current tag from `league.json`/`week` rosters and a source.
4. Read the diff (`git diff --stat`, then skim). If a quick update touched anything outside the allowed sections, revert that part.

## Commit and push

```
git add index.html data/espn/team-map.json
git commit -m "<message>"
git pull --rebase origin main
git push origin HEAD:main
```

End commit messages with the attribution trailer your session instructions specify. GitHub Pages deploys within a couple of minutes.

## When something fails

- ESPN cookies expired → see step 3 of "Before every run".
- Action can't be dispatched or polled → use the existing `data/espn` files if they're under 12 hours old; otherwise skip the run and say why.
- Audit fails and you can't fix it in the run → don't push. Report the failure lines.
- Drive archive fails → the push still stands; mention it in the run summary.
