from datetime import datetime, timezone
import json
import os
import time

import requests


GITHUB_API = "https://api.github.com"
REPOS_PER_PAGE = 100
COMMITS_PER_PAGE = 100
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "0.2"))

# Optional repo noise filters.
INCLUDE_FORKS = os.getenv("INCLUDE_FORKS", "true").strip().lower() in {"1", "true", "yes", "y"}
INCLUDE_ARCHIVED = os.getenv("INCLUDE_ARCHIVED", "true").strip().lower() in {"1", "true", "yes", "y"}

ACCOUNTS = [
    {
        "username": "AhmedAbubaker98",
        "token": os.getenv("TOKEN_PERSONAL"),
        "emails": ["ahmedkata@gmail.com"],
    },
    {
        "username": "AhmedElagibMarkaba",
        "token": os.getenv("TOKEN_WORK"),
        "emails": ["ahmedelagibmarkaba@gmail.com"],
    },
]


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def _extract_error_message(data):
    if isinstance(data, dict):
        if "message" in data and data["message"]:
            return str(data["message"])
        if isinstance(data.get("errors"), list):
            return "; ".join(str(err) for err in data["errors"])
    return str(data)


def _normalize_emails(emails):
    return {email.strip().lower() for email in emails if email and email.strip()}


def _wait_for_rate_limit(response):
    remaining = response.headers.get("X-RateLimit-Remaining")
    reset = response.headers.get("X-RateLimit-Reset")

    if response.status_code == 403 and remaining == "0" and reset:
        reset_epoch = int(reset)
        sleep_seconds = max(reset_epoch - int(time.time()), 1) + 1
        reset_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc).isoformat()
        print(f"Rate limit reached. Sleeping {sleep_seconds}s until {reset_at}.")
        time.sleep(sleep_seconds)
        return True

    return False


def _get_json_with_retries(url, token, params=None, max_retries=3):
    attempt = 0

    while True:
        response = requests.get(url, headers=_headers(token), params=params, timeout=30)

        try:
            data = response.json()
        except ValueError:
            data = {}

        if _wait_for_rate_limit(response):
            continue

        if response.status_code in {500, 502, 503, 504} and attempt < max_retries:
            backoff = 2 ** attempt
            print(f"Transient error {response.status_code} for {url}. Retrying in {backoff}s.")
            time.sleep(backoff)
            attempt += 1
            continue

        return response, data


def _validate_accounts(accounts):
    placeholder_emails = {"ahmedkata@gmail.com", "ahmedelagibmarkaba@gmail.com"}

    for account in accounts:
        username = account.get("username", "<unknown>")

        if not account.get("token"):
            raise ValueError(f"Missing token for {username}. Set the token environment variable.")

        normalized_emails = sorted(_normalize_emails(account.get("emails", [])))
        if not normalized_emails:
            raise ValueError(f"Missing commit emails for {username}. Update ACCOUNTS with real emails.")
        if any(email in placeholder_emails for email in normalized_emails):
            raise ValueError(f"Placeholder email found for {username}. Replace it with real commit email(s).")

        account["emails"] = normalized_emails


def get_repos(token):
    repos = []
    seen = set()
    page = 1

    while True:
        response, data = _get_json_with_retries(
            f"{GITHUB_API}/user/repos",
            token,
            params={
                "per_page": REPOS_PER_PAGE,
                "page": page,
                "affiliation": "owner,collaborator,organization_member",
            },
        )

        if not response.ok:
            raise RuntimeError(
                f"Repo listing failed (HTTP {response.status_code}): {_extract_error_message(data)}"
            )

        if not isinstance(data, list) or not data:
            break

        for repo in data:
            full_name = repo.get("full_name")
            if not full_name or full_name in seen:
                continue
            if not INCLUDE_FORKS and repo.get("fork"):
                continue
            if not INCLUDE_ARCHIVED and repo.get("archived"):
                continue

            seen.add(full_name)
            repos.append(repo)

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return repos


def count_commits(repo_full_name, token, emails):
    commits = 0
    page = 1

    while True:
        response, data = _get_json_with_retries(
            f"{GITHUB_API}/repos/{repo_full_name}/commits",
            token,
            params={"per_page": COMMITS_PER_PAGE, "page": page},
        )

        if response.status_code in {404, 409}:
            return commits

        if not response.ok:
            raise RuntimeError(
                f"Commit fetch failed (HTTP {response.status_code}): {_extract_error_message(data)}"
            )

        if not isinstance(data, list) or not data:
            break

        for commit in data:
            email = (
                commit.get("commit", {})
                .get("author", {})
                .get("email", "")
                .strip()
                .lower()
            )
            if email in emails:
                commits += 1

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return commits


def aggregate():
    totals = {"commits": 0}
    breakdown = {}
    per_repo = {}

    _validate_accounts(ACCOUNTS)

    for account in ACCOUNTS:
        username = account["username"]
        token = account["token"]
        emails = set(account["emails"])

        print(f"Scanning repos for {username}...")
        repos = get_repos(token)

        user_total = 0
        user_repo_counts = {}

        for idx, repo in enumerate(repos, start=1):
            full_name = repo["full_name"]
            try:
                commit_count = count_commits(full_name, token, emails)
                user_total += commit_count
                user_repo_counts[full_name] = commit_count
                print(f"[{idx}/{len(repos)}] {full_name}: {commit_count}")
            except Exception as exc:
                print(f"Skip {full_name}: {exc}")

        breakdown[username] = {
            "commits": user_total,
            "repos_scanned": len(repos),
            "emails": account["emails"],
        }
        per_repo[username] = user_repo_counts
        totals["commits"] += user_total

    return totals, breakdown, per_repo


def save(totals, breakdown, per_repo):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "accurate_commit_history",
        "totals": totals,
        "breakdown": breakdown,
        "per_repo": per_repo,
    }

    with open("stats.json", "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    with open("stats.md", "w", encoding="utf-8") as file:
        file.write("## Combined GitHub Stats (Accurate Commit Mode)\n\n")

        file.write("### Totals\n")
        for key, value in totals.items():
            file.write(f"- {key}: {value}\n")

        file.write("\n### Breakdown\n")
        for username, stats in breakdown.items():
            file.write(f"\n**{username}**\n")
            file.write(f"- commits: {stats['commits']}\n")
            file.write(f"- repos_scanned: {stats['repos_scanned']}\n")
            file.write(f"- emails: {', '.join(stats['emails'])}\n")


if __name__ == "__main__":
    totals_result, breakdown_result, per_repo_result = aggregate()
    save(totals_result, breakdown_result, per_repo_result)