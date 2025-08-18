import os, time, requests

# ── CONFIG ───────────────────────────────────────────────────────────────
AZURE_ORG    = "WFRD-RDE-DWC-Software"            # e.g. "MyCompany"
AZURE_PROJ   = "OmniStack"                        # e.g. "Website"
PAT          = os.environ["AZURE_PAT"]            # Personal Access Token
SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]

POLL_INTERVAL = 120                      # seconds between checks
COLUMN_NAME   = "Ready for QA"           # the board column to watch

seen_ids = set()

# ── HELPERS ──────────────────────────────────────────────────────────────
def azure_get(path, params=None):
    url = f"https://dev.azure.com/{AZURE_ORG}/{AZURE_PROJ}/_apis/{path}"
    response = requests.get(url, auth=('', PAT), params=params)
    print('Debugging')
    print('response.status_code')   # 203
    print(response.status_code)   # 203
    print('response.text')          # Raw response body as text
    print(response.text)          # Raw response body as text
    print('response.json()')        # If it’s JSON data
    print(response.json())        # If it’s JSON data
    print('response.headers')       # Response headers
    print(response.headers)       # Response headers
    print('temp')
    print(temp)
    return temp.json()

def find_column_id(board_id, column_name):
    cols = azure_get(f"work/boards/{board_id}/columns", {"api-version":"6.0-preview.1"})["value"]
    for c in cols:
        if c["name"] == column_name:
            return c["id"]
    raise ValueError(f"Column not found: {column_name}")

def query_items_in_column(board_id, column_id):
    # WIQL: find all items on that column
    wiql = {
      "query": f"SELECT [System.Id],[System.Title] "
             f"FROM workitems "
             f"WHERE [System.BoardColumn] = '{COLUMN_NAME}'"
    }
    res = requests.post(
      f"https://dev.azure.com/{AZURE_ORG}/{AZURE_PROJ}/_apis/wit/wiql?api-version=6.0",
      json=wiql, auth=('', PAT)
    ).json()
    return res["workItems"]

def post_to_slack(text):
    requests.post(SLACK_WEBHOOK, json={"text": text, "icon_emoji": ":robot_face:"})

# ── MAIN LOOP ────────────────────────────────────────────────────────────
# 1. Lookup the default board for your team (or hard-code its ID)
board = azure_get(f"work/boards", {"api-version":"6.0-preview.1"})["value"][0]
board_id = board["id"]
column_id = find_column_id(board_id, COLUMN_NAME)

while True:
    items = query_items_in_column(board_id, column_id)
    for itm in items:
        wid = itm["id"]
        if wid not in seen_ids:
            seen_ids.add(wid)
            # fetch full details
            details = azure_get(f"wit/workitems/{wid}", {"api-version":"6.0"})
            title   = details["fields"].get("System.Title")
            url     = details["_links"]["html"]["href"]
            post_to_slack(
              f":excitedstar: *Ticket ready for testing: *<{url}|#{wid} – {title}>"
            )
    time.sleep(POLL_INTERVAL)
