import os
import time
import requests

# ── CONFIG ───────────────────────────────────────────────────────────────
AZURE_ORG     = "WFRD-RDE-DWC-Software"           # Organization
AZURE_PROJ    = "OmniStack"                        # Project
TEAM_NAME     = "WASP"                             # Team from your URL
BOARD_NAME    = "Stories"                          # Board from your URL
COLUMN_NAME   = "Ready for QA"                     # Column to watch

PAT           = os.environ["AZURE_PAT"]            # Personal Access Token
SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]        # Slack Incoming Webhook

POLL_INTERVAL = 120                                 # seconds between checks

# Track seen work item IDs per project to avoid duplicate alerts
seen_ids = set()

# ── HTTP HELPERS ─────────────────────────────────────────────────────────
def _request(method, path, *, params=None, json=None):
    """Low-level HTTP helper that prefixes org base URL and raises on errors."""
    base = f"https://dev.azure.com/{AZURE_ORG}"
    url  = f"{base}/{path.lstrip('/')}"
    resp = requests.request(method, url, auth=('', PAT), params=params, json=json)
    resp.raise_for_status()
    if resp.content:
        return resp.json()
    return {}

def azure_get(path, params=None):
    return _request("GET", path, params=params)

def azure_post(path, json=None, params=None):
    return _request("POST", path, params=params, json=json)

def post_to_slack(text):
    try:
        requests.post(SLACK_WEBHOOK, json={"text": text, "icon_emoji": ":robot_face:"}, timeout=10)
    except Exception as e:
        print(f"[warn] Slack post failed: {e}")

# ── BOARD + COLUMN LOOKUP (TEAM-SCOPED) ──────────────────────────────────
def get_team_boards(project, team):
    """List boards for a specific team."""
    data = azure_get(f"{project}/{team}/_apis/work/boards", {"api-version": "6.0-preview.1"})
    return data.get("value", [])

def get_board_id_by_name(project, team, board_name):
    boards = get_team_boards(project, team)
    board = next((b for b in boards if b.get("name") == board_name), None)
    if not board:
        raise RuntimeError(f"Board '{board_name}' not found for team '{team}'. "
                           f"Available: {[b.get('name') for b in boards]}")
    return board["id"], board["name"]

def get_board_columns(project, team, board_id):
    data = azure_get(f"{project}/{team}/_apis/work/boards/{board_id}/columns",
                     {"api-version": "6.0-preview.1"})
    return data.get("value", [])

def get_column_id_by_name(project, team, board_id, column_name):
    cols = get_board_columns(project, team, board_id)
    col = next((c for c in cols if c.get("name") == column_name), None)
    if not col:
        raise RuntimeError(f"Column '{column_name}' not found on board id {board_id}. "
                           f"Available: {[c.get('name') for c in cols]}")
    return col["id"], col["name"]

# ── TEAM AREA SCOPE (to limit WIQL to this board’s area) ─────────────────
def get_team_area_predicate(project, team):
    """
    Builds a WIQL WHERE-clause fragment that restricts results to the team’s Area Paths.
    Uses the team’s Team Field Values (typically 'System.AreaPath').
    """
    data = azure_get(f"{project}/{team}/_apis/work/teamsettings/teamfieldvalues",
                     {"api-version": "6.0"})
    # Expect structure like:
    # {
    #   "field": {"referenceName":"System.AreaPath", ...},
    #   "defaultValue": "OmniStack\\WASP",
    #   "values": [{"value": "OmniStack\\WASP", "includeChildren": true}, ...]
    # }
    values = data.get("values", [])
    if not values:
        # Fallback: constrain to the project root if team settings aren’t accessible
        return f"[System.TeamProject] = '{AZURE_PROJ}'"

    parts = []
    for v in values:
        path = v.get("value")
        include_children = v.get("includeChildren", False)
        if not path:
            continue
        if include_children:
            parts.append(f"[System.AreaPath] UNDER '{path}'")
        else:
            parts.append(f"[System.AreaPath] = '{path}'")

    # If nothing usable, at least limit by project
    if not parts:
        return f"[System.TeamProject] = '{AZURE_PROJ}'"

    # Combine multiple area scopes with OR and wrap in parentheses
    return "(" + " OR ".join(parts) + ")"

# ── WIQL QUERY (PROJECT-SCOPED, FILTERED BY COLUMN + TEAM AREA) ──────────
def query_items_in_board_column(project, column_name, team_area_predicate):
    wiql = {
        "query": (
            "SELECT [System.Id], [System.Title] "
            "FROM workitems "
            f"WHERE [System.TeamProject] = '{project}' "
            f"AND [System.BoardColumn] = '{column_name}' "
            f"AND {team_area_predicate}"
        )
    }
    res = azure_post(f"{project}/_apis/wit/wiql", json=wiql, params={"api-version": "6.0"})
    return res.get("workItems", [])

def get_work_item(project, wid):
    return azure_get(f"{project}/_apis/wit/workitems/{wid}", {"api-version":"6.0"})

# ── MAIN ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Resolve board + column under the team
    board_id, resolved_board_name = get_board_id_by_name(AZURE_PROJ, TEAM_NAME, BOARD_NAME)
    column_id, resolved_col_name = get_column_id_by_name(AZURE_PROJ, TEAM_NAME, board_id, COLUMN_NAME)

    print(f"Monitoring Org='{AZURE_ORG}', Project='{AZURE_PROJ}', Team='{TEAM_NAME}'")
    print(f"Board='{resolved_board_name}' (id={board_id}), Column='{resolved_col_name}'")

    # Build the area-path predicate from the team settings so WIQL matches this board’s scope
    area_predicate = get_team_area_predicate(AZURE_PROJ, TEAM_NAME)
    print(f"Area predicate: {area_predicate}")

    while True:
        try:
            items = query_items_in_board_column(AZURE_PROJ, COLUMN_NAME, area_predicate)
            for itm in items:
                wid = itm["id"]
                if wid not in seen_ids:
                    seen_ids.add(wid)
                    details = get_work_item(AZURE_PROJ, wid)
                    title   = details["fields"].get("System.Title")
                    url     = details["_links"]["html"]["href"]
                    post_to_slack(
                        f":excitedstar: *[{TEAM_NAME} · {BOARD_NAME}] Ticket ready for testing:* "
                        f"<{url}|#{wid} – {title}>"
                    )
        except Exception as e:
            print(f"[error] {e}")

        time.sleep(POLL_INTERVAL)
