import requests
import os
import json
from datetime import datetime

# 🔐 Tokens (from GitHub Secrets)
ACCOUNTS = [
    {
        "username": "AhmedAbubaker98",
        "token": os.getenv("TOKEN_PERSONAL")
    },
    {
        "username": "AhmedElagibMarkaba",
        "token": os.getenv("TOKEN_WORK")
    }
]

# ⏳ Extend contribution window (adjust as needed)
START_DATE = "2018-01-01T00:00:00Z"
END_DATE = datetime.utcnow().isoformat() + "Z"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      restrictedContributionsCount
    }
    repositories(privacy: PUBLIC) {
      totalCount
    }
  }
}
"""

def fetch_user_data(username, token):
    headers = {
        "Authorization": f"Bearer {token}"
    }

    response = requests.post(
        "https://api.github.com/graphql",
        json={
            "query": QUERY,
            "variables": {
                "login": username,
                "from": START_DATE,
                "to": END_DATE
            }
        },
        headers=headers
    )

    data = response.json()

    if "errors" in data:
        raise Exception(f"Error fetching {username}: {data['errors']}")

    return data["data"]["user"]


def aggregate():
    totals = {
        "commits": 0,
        "prs": 0,
        "issues": 0,
        "repos": 0,
        "restricted": 0
    }

    breakdown = {}

    for acc in ACCOUNTS:
        username = acc["username"]
        token = acc["token"]

        print(f"Fetching {username}...")
        data = fetch_user_data(username, token)

        contribs = data["contributionsCollection"]

        user_stats = {
            "commits": contribs["totalCommitContributions"],
            "prs": contribs["totalPullRequestContributions"],
            "issues": contribs["totalIssueContributions"],
            "repos": data["repositories"]["totalCount"],
            "restricted": contribs["restrictedContributionsCount"]
        }

        breakdown[username] = user_stats

        # Aggregate totals
        for key in totals:
            totals[key] += user_stats[key]

    return totals, breakdown


def save_outputs(totals, breakdown):
    # JSON output
    with open("stats.json", "w") as f:
        json.dump({
            "totals": totals,
            "breakdown": breakdown
        }, f, indent=2)

    # Markdown output (for README)
    with open("stats.md", "w") as f:
        f.write(f"""
## 🚀 Combined GitHub Stats

### 🧮 Totals
- 💻 Commits: {totals['commits']}
- 🔁 Pull Requests: {totals['prs']}
- 🐛 Issues: {totals['issues']}
- 📦 Repositories: {totals['repos']}

### 🔒 Private Contributions
- 🔐 Private/Restricted Commits: {totals['restricted']}

---

### 📊 Breakdown

""")

        for user, stats in breakdown.items():
            f.write(f"""
#### 👤 {user}
- 💻 Commits: {stats['commits']}
- 🔁 PRs: {stats['prs']}
- 🐛 Issues: {stats['issues']}
- 📦 Repos: {stats['repos']}
- 🔐 Private: {stats['restricted']}
""")

    print("✅ stats.json and stats.md updated.")


if __name__ == "__main__":
    totals, breakdown = aggregate()
    save_outputs(totals, breakdown)