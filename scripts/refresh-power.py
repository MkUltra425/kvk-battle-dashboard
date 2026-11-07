import json, os, re, time, urllib.request
from pathlib import Path

page = Path("index.html")
source = page.read_text()
fields = json.loads(re.search(r"const MEMBER_POWER_FIELDS = (\[[^\n;]+\]);", source)[1])
# Prefer a repository secret; support the key already configured by this dashboard.
match = re.search(r'const apiKey = "([^"]+)"', source)
key = os.environ.get("MIGHTPULSE_API_KEY") or (match[1] if match else "")
if not key:
    raise RuntimeError("Configure the MIGHTPULSE_API_KEY repository secret")
boards = {}
for field in fields:
    req = urllib.request.Request(
        "https://api.mightpulse.com/v1/kingdoms/1884/ranks?board=" + field + "&limit=100",
        headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        data = json.load(response)
    if isinstance(data, dict) and data.get("ok") is False:
        raise RuntimeError("Mightpulse rejected board " + field)
    rows = []
    def collect(value):
        if isinstance(value, dict):
            if value.get("governor_id") is not None and value.get("score") is not None:
                score = float(value["score"])
                if score >= 0:
                    rows.append({"governor_id": str(value["governor_id"]), "score": score})
            else:
                for child in value.values():
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(data)
    if not rows:
        raise RuntimeError("No readable rows for " + field)
    boards[field] = {"rows": rows}
    time.sleep(1)
from datetime import datetime, timezone
payload = {"fetched_at": datetime.now(timezone.utc).isoformat(), "boards": boards}
# Publish only after all boards succeed.
Path("power-latest.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n")
start = source.index("  async function refreshMemberPower() {")
end = source.index("\n  async function loadAvatars()", start)
replacement = r'''  async function refreshMemberPower() {
    if (!requireEdit() || powerSyncing) return;
    setPowerSyncing(true);
    const updates = {};
    let matched = 0;
    try {
      setPowerSyncMsg("Loading latest server-fetched Mightpulse power data…");
      const response = await fetch("https://raw.githubusercontent.com/MkUltra425/kvk-battle-dashboard/main/power-latest.json?t=" + Date.now(), {cache:"no-store"});
      if (!response.ok) throw new Error("Power data is not available yet. Check the Power data refresh workflow in GitHub Actions.");
      const data = await response.json();
      const fetched = Date.parse(data.fetched_at);
      if (!Number.isFinite(fetched) || Date.now() - fetched > 2 * 60 * 60 * 1000)
        throw new Error("The server power data is over two hours old. Check GitHub Actions before retrying.");
      for (const field of MEMBER_POWER_FIELDS) {
        const rows = data.boards?.[field]?.rows;
        if (!Array.isArray(rows) || !rows.length) throw new Error("Incomplete power data: " + field);
        const scores = new Map();
        for (const row of rows) {
          const score = Number(row.score);
          if (!row.governor_id || !Number.isFinite(score) || score < 0) throw new Error("Invalid power data: " + field);
          scores.set(String(row.governor_id), score);
        }
        for (const m of members) {
          const id = m.governorId || (/^\d{7,10}$/.test(String(m.id)) ? m.id : null);
          if (!id) continue;
          const score = scores.get(String(id));
          updates["members/" + m.id + "/" + field] = score === undefined ? null : score;
          if (score !== undefined) matched++;
        }
      }
      for (const m of members) {
        if (Object.keys(updates).some(path => path.startsWith("members/" + m.id + "/"))) {
          updates["members/" + m.id + "/powerUpdatedAt"] = data.fetched_at;
          updates["members/" + m.id + "/powerSource"] = "Mightpulse";
        }
      }
      if (Object.keys(updates).length) await db.ref().update(updates);
      setPowerSyncMsg("Updated " + matched + " power values. Server fetched: " + new Date(fetched).toUTCString() + ". Fetch runs every 30 minutes; source boards may be cached. — means outside the returned Top 100.");
    } catch (e) {
      setPowerSyncMsg((e.message === "Failed to fetch" ? "Could not download the server power data. Check your connection and retry." : e.message) + " No partial refresh was saved.");
    } finally { setPowerSyncing(false); }
  }
'''
replacement = replacement.replace("    setPowerSyncing(true);", "    const apiKey = " + json.dumps(key) + ";\n    setPowerSyncing(true);", 1)
page.write_text(source[:start] + replacement + source[end:])
print("Validated all eight power boards; updated snapshot and refresh button.")
