#!/usr/bin/env python3
"""Pull Big Ballers league data from ESPN's fantasy API into data/espn/.

Runs in GitHub Actions with two repo secrets:
  ESPN_S2   - the espn_s2 cookie from a signed-in fantasy.espn.com session
  ESPN_SWID - the SWID cookie (with or without the curly braces)

Writes trimmed, stable JSON (sorted keys) so a file only changes when the
league actually changes. Raw responses go to data/espn/_raw/ (gitignored) and
are uploaded as a workflow artifact for debugging.

Stdlib only. Usage: python3 scripts/fetch_espn.py [--weeks all|recent]
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LEAGUE_ID = 432801
SEASON = int(os.environ.get("ESPN_SEASON", "2026"))
BASE = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}"
LEAGUE = f"{BASE}/segments/0/leagues/{LEAGUE_ID}"
OUT = Path(__file__).resolve().parent.parent / "data" / "espn"
RAW = OUT / "_raw"

SLOT = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 20: "BE", 21: "IR", 23: "FLEX"}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
PRO = {0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
       9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
       17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
       25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}


def fail(msg):
    # ::error:: shows as a run annotation, readable via the check-runs API.
    print(f"::error title=ESPN fetch failed::{msg}")
    sys.exit(msg)


def cookies():
    s2 = os.environ.get("ESPN_S2", "").strip()
    swid = os.environ.get("ESPN_SWID", "").strip()
    if not s2 or not swid:
        fail("ESPN_S2 / ESPN_SWID secrets are missing. Add them under repo Settings > Secrets and variables > Actions.")
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return f"espn_s2={s2}; SWID={swid}"


COOKIE = None


def get(url, views=(), params=None, xfilter=None, name=None):
    q = [("view", v) for v in views] + list((params or {}).items())
    full = url + ("?" + "&".join(f"{k}={v}" for k, v in q) if q else "")
    headers = {"Cookie": COOKIE, "Accept": "application/json",
               "User-Agent": "Mozilla/5.0 (bigballers dashboard fetcher)"}
    if xfilter is not None:
        headers["x-fantasy-filter"] = json.dumps(xfilter)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers=headers), timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            if name:
                RAW.mkdir(parents=True, exist_ok=True)
                (RAW / f"{name}.json").write_text(json.dumps(data))
            return data
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                fail(f"ESPN refused the request (HTTP {e.code}). The espn_s2/SWID cookies are wrong or expired; "
                         "copy fresh ones from fantasy.espn.com into the repo secrets.")
            if attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def write(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    text = json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
        print(f"updated {name}")


def player_row(entry, period=None):
    """One roster entry -> flat dict. Works for mRoster and boxscore rosters."""
    ppe = entry.get("playerPoolEntry") or {}
    p = ppe.get("player") or entry.get("player") or {}
    row = {
        "playerId": entry.get("playerId", p.get("id")),
        "name": p.get("fullName"),
        "pos": POS.get(p.get("defaultPositionId"), p.get("defaultPositionId")),
        "nfl": PRO.get(p.get("proTeamId"), p.get("proTeamId")),
        "slot": SLOT.get(entry.get("lineupSlotId"), entry.get("lineupSlotId")),
        "injuryStatus": p.get("injuryStatus") or ("ACTIVE" if p.get("injured") is False else None),
        "injured": p.get("injured"),
        "acquisitionType": entry.get("acquisitionType"),
        "pctOwned": round((p.get("ownership") or {}).get("percentOwned", 0) or 0, 1),
        "pctStarted": round((p.get("ownership") or {}).get("percentStarted", 0) or 0, 1),
    }
    if period is not None:
        actual = proj = None
        for s in p.get("stats") or []:
            if s.get("scoringPeriodId") != period or s.get("statSplitTypeId") != 1:
                continue
            if s.get("statSourceId") == 0:
                actual = s.get("appliedTotal")
            elif s.get("statSourceId") == 1:
                proj = s.get("appliedTotal")
        if actual is None and "appliedStatTotal" in ppe:
            actual = ppe.get("appliedStatTotal")
        row["points"] = round(actual, 2) if isinstance(actual, (int, float)) else actual
        row["proj"] = round(proj, 2) if isinstance(proj, (int, float)) else proj
    return row


def team_name(t):
    return t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()


def main():
    global COOKIE
    COOKIE = cookies()
    mode = sys.argv[sys.argv.index("--weeks") + 1] if "--weeks" in sys.argv else "recent"

    lg = get(LEAGUE, ["mTeam", "mRoster", "mSettings", "mStatus", "mStandings", "mMatchupScore"], name="league")
    status = lg.get("status") or {}
    settings = lg.get("settings") or {}
    cur_period = lg.get("scoringPeriodId") or status.get("currentMatchupPeriod")
    cur_matchup = status.get("currentMatchupPeriod") or cur_period
    members = {m.get("id"): f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() for m in lg.get("members") or []}

    teams = []
    for t in lg.get("teams") or []:
        rec = ((t.get("record") or {}).get("overall")) or {}
        teams.append({
            "teamId": t.get("id"), "name": team_name(t), "abbrev": t.get("abbrev"),
            "managers": [members.get(o, o) for o in t.get("owners") or []],
            "wins": rec.get("wins"), "losses": rec.get("losses"), "ties": rec.get("ties"),
            "pointsFor": round(rec.get("pointsFor", 0) or 0, 2),
            "pointsAgainst": round(rec.get("pointsAgainst", 0) or 0, 2),
            "streak": f"{rec.get('streakType', '')} {rec.get('streakLength', '')}".strip(),
            "playoffSeed": t.get("playoffSeed"), "waiverRank": t.get("waiverRank"),
            "faabSpent": (t.get("transactionCounter") or {}).get("acquisitionBudgetSpent"),
            "moves": {k: (t.get("transactionCounter") or {}).get(k) for k in ("acquisitions", "drops", "trades", "moveToActive", "moveToIR")},
            "roster": sorted((player_row(e) for e in ((t.get("roster") or {}).get("entries") or [])),
                             key=lambda r: (str(r["slot"]), str(r["name"]))),
        })
    teams.sort(key=lambda t: t["teamId"])

    schedule = [{
        "week": m.get("matchupPeriodId"), "winner": m.get("winner"),
        "home": (m.get("home") or {}).get("teamId"), "homePoints": (m.get("home") or {}).get("totalPoints"),
        "away": (m.get("away") or {}).get("teamId"), "awayPoints": (m.get("away") or {}).get("totalPoints"),
        "playoffTierType": m.get("playoffTierType"),
    } for m in lg.get("schedule") or []]

    write("league.json", {
        "leagueId": LEAGUE_ID, "season": SEASON, "name": settings.get("name"),
        "currentScoringPeriod": cur_period, "currentMatchupPeriod": cur_matchup,
        "finalScoringPeriod": status.get("finalScoringPeriod"),
        "latestScoringPeriod": status.get("latestScoringPeriod"),
        "isActive": status.get("isActive"),
        "waiverProcessStatus": status.get("waiverProcessStatus"),
        "tradeDeadline": (settings.get("tradeSettings") or {}).get("deadlineDate"),
        "teams": teams, "schedule": schedule,
    })

    # Box scores: every completed/in-progress matchup week. "recent" refreshes the
    # current and previous week (stat corrections) plus any week never fetched.
    last = cur_matchup or 1
    for wk in range(1, last + 1):
        path = OUT / f"week-{wk:02d}.json"
        if mode != "all" and path.exists() and wk < last - 1:
            continue
        box = get(LEAGUE, ["mMatchupScore", "mBoxscore", "mScoreboard", "mRoster"],
                  params={"scoringPeriodId": wk},
                  xfilter={"schedule": {"filterMatchupPeriodIds": {"value": [wk]}}}, name=f"week-{wk:02d}")
        games = []
        for m in box.get("schedule") or []:
            if m.get("matchupPeriodId") != wk:
                continue
            g = {"week": wk, "winner": m.get("winner")}
            for side in ("home", "away"):
                s = m.get(side) or {}
                ros = (s.get("rosterForCurrentScoringPeriod") or s.get("rosterForMatchupPeriod") or {}).get("entries") or []
                g[side] = {
                    "teamId": s.get("teamId"), "points": s.get("totalPoints"),
                    "pointsLive": s.get("totalPointsLive"), "projected": s.get("totalProjectedPointsLive"),
                    "players": [player_row(e, wk) for e in ros],
                }
            games.append(g)
        write(f"week-{wk:02d}.json", {"week": wk, "complete": wk < (status.get("latestScoringPeriod") or cur_period or 0), "games": games})

        tx = get(LEAGUE, ["mTransactions2"], params={"scoringPeriodId": wk}, name=f"tx-{wk:02d}")
        rows = []
        for t in tx.get("transactions") or []:
            # ROSTER = lineup shuffles; skip them, keep adds/drops/waivers/trades.
            if t.get("status") not in ("EXECUTED", None) or t.get("type") == "ROSTER":
                continue
            rows.append({
                "id": t.get("id"), "type": t.get("type"), "teamId": t.get("teamId"),
                "date": datetime.fromtimestamp((t.get("processDate") or t.get("proposedDate") or 0) / 1000, timezone.utc).isoformat(),
                "bid": t.get("bidAmount"),
                "items": [{"type": i.get("type"), "playerId": i.get("playerId"),
                           "from": i.get("fromTeamId"), "to": i.get("toTeamId")} for i in t.get("items") or []],
            })
        rows.sort(key=lambda r: (r["date"], str(r["id"])))
        write(f"transactions-{wk:02d}.json", {"week": wk, "transactions": rows})

    # Top available players (free agents + waivers) by ownership, with injury tags.
    fa = get(LEAGUE, ["kona_player_info"], params={"scoringPeriodId": cur_period},
             xfilter={"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                                  "limit": 60, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}},
             name="free-agents")
    write("free-agents.json", {"week": cur_period, "players": [
        player_row({"playerId": p.get("id"), "player": p.get("player")}) for p in fa.get("players") or []]})

    # NFL schedule (byes, kickoff times) for the season.
    pro = get(BASE, ["proTeamSchedules_wl"], name="pro-schedule")
    teams_sched = ((pro.get("settings") or {}).get("proTeams")) or []
    write("nfl-schedule.json", {"teams": [{
        "abbr": PRO.get(t.get("id"), t.get("abbrev")), "bye": t.get("byeWeek"),
        "games": sorted([{"week": int(k), "home": PRO.get(g.get("homeProTeamId")), "away": PRO.get(g.get("awayProTeamId")),
                          "kickoff": datetime.fromtimestamp((g.get("date") or 0) / 1000, timezone.utc).isoformat()}
                         for k, gs in (t.get("proGamesByScoringPeriod") or {}).items() for g in gs],
                        key=lambda g: g["week"]),
    } for t in teams_sched if t.get("id")]})

    print(f"fetched league {LEAGUE_ID}: scoring period {cur_period}, matchup period {cur_matchup}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # network/ESPN format problems: say what broke
        fail(f"{type(e).__name__}: {e}")
