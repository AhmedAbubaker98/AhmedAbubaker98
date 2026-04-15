from datetime import datetime, timedelta
import requests
import os
import json

ACCOUNTS = [
    {"username": "your_personal", "token": os.getenv("TOKEN_PERSONAL")},
    {"username": "your_work", "token": os.getenv("TOKEN_WORK")}
]

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      restrictedContributionsCount
    }
  }
}
"""

def fetch_range(username, token, start, end):
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.post(
        "https://api.github.com/graphql",
        json={
            "query": QUERY,
            "variables": {
                "login": username,
                "from": start,
                "to": end
            }
        },
        headers=headers
    )

    data = r.json()

    if "errors" in data:
        raise Exception(data["errors"])

    return data["data"]["user"]["contributionsCollection"]


def yearly_chunks(start_year=2019):
    now = datetime.utcnow()
    chunks = []

    for year in range(start_year, now.year + 1):
        start = datetime(year, 1, 1)
        end = datetime(year + 1, 1, 1)

        if end > now:
            end = now

        chunks.append((start.isoformat() + "Z", end.isoformat() + "Z"))

    return chunks


def aggregate():
    totals = {
        "commits": 0,
        "prs": 0,
        "issues": 0,
        "restricted": 0
    }

    breakdown = {}

    chunks = yearly_chunks(2019)

    for acc in ACCOUNTS:
        u = acc["username"]
        t = acc["token"]

        user_total = {
            "commits": 0,
            "prs": 0,
            "issues": 0,
            "restricted": 0
        }

        print(f"Fetching {u}...")

        for start, end in chunks:
            c = fetch_range(u, t, start, end)

            user_total["commits"] += c["totalCommitContributions"]
            user_total["prs"] += c["totalPullRequestContributions"]
            user_total["issues"] += c["totalIssueContributions"]
            user_total["restricted"] += c["restrictedContributionsCount"]

        breakdown[u] = user_total

        for k in totals:
            totals[k] += user_total[k]

    return totals, breakdown


def save(totals, breakdown):
    with open("stats.json", "w") as f:
        json.dump({"totals": totals, "breakdown": breakdown}, f, indent=2)

    with open("stats.md", "w") as f:
        f.write("## 🚀 Combined GitHub Stats\n\n")

        f.write("### Totals\n")
        for k, v in totals.items():
            f.write(f"- {k}: {v}\n")

        f.write("\n### Breakdown\n")
        for u, s in breakdown.items():
            f.write(f"\n**{u}**\n")
            for k, v in s.items():
                f.write(f"- {k}: {v}\n")


if __name__ == "__main__":
    totals, breakdown = aggregate()
    save(totals, breakdown)