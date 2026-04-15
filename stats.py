import csv
from datetime import datetime, timezone
import json
import os
import time

import requests
from requests import RequestException


def _load_dotenv(dotenv_path=".env"):
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if key and key not in os.environ:
                os.environ[key] = value


def _env_bool(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _bump_counter(counter, key, amount=1):
    counter[key] = counter.get(key, 0) + amount


def _merge_counters(target, source):
    for key, value in source.items():
        _bump_counter(target, key, value)


def _sorted_counter(counter):
    return dict(sorted(counter.items(), key=lambda item: item[0]))


def _extract_commit_day(commit_payload):
    commit_meta = _safe_dict(commit_payload.get("commit"))
    author_meta = _safe_dict(commit_meta.get("author"))
    committer_meta = _safe_dict(commit_meta.get("committer"))

    raw_date = author_meta.get("date") or committer_meta.get("date")
    if not raw_date:
        return None

    date_text = str(raw_date).strip()
    if not date_text:
        return None

    if date_text.endswith("Z"):
        date_text = date_text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(date_text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc).date().isoformat()


def _to_monthly_counts(daily_counts):
    monthly = {}
    for day, count in daily_counts.items():
        month = day[:7]
        _bump_counter(monthly, month, count)
    return _sorted_counter(monthly)


def _to_iso_z(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc_datetime(value, end_of_day=False):
    if not value:
        return None

    text = value.strip()

    if "T" not in text:
        suffix = "23:59:59+00:00" if end_of_day else "00:00:00+00:00"
        text = f"{text}T{suffix}"

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _extract_error_message(data):
    payload = _safe_dict(data)

    message = payload.get("message")
    if message:
        return str(message)

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(item) for item in errors)

    return str(data)


_load_dotenv()


GITHUB_API = "https://api.github.com"
GRAPHQL_API = f"{GITHUB_API}/graphql"
REPOS_PER_PAGE = 100
COMMITS_PER_PAGE = 100
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "0.2"))

# Optional repo noise filters.
INCLUDE_FORKS = _env_bool("INCLUDE_FORKS", True)
INCLUDE_ARCHIVED = _env_bool("INCLUDE_ARCHIVED", True)

# Date filter shared by both accurate and fallback modes.
STATS_FROM_DT = _parse_utc_datetime(os.getenv("STATS_FROM", "2019-01-01"), end_of_day=False)
STATS_TO_DT = _parse_utc_datetime(os.getenv("STATS_TO"), end_of_day=True) or datetime.now(timezone.utc)
if STATS_TO_DT <= STATS_FROM_DT:
    raise ValueError("STATS_TO must be later than STATS_FROM.")

STATS_FROM_ISO = _to_iso_z(STATS_FROM_DT)
STATS_TO_ISO = _to_iso_z(STATS_TO_DT)

# Hybrid strategy:
# - accurate: only repo commit scan
# - fallback: only GraphQL contributions commit totals
# - higher: choose higher of accurate and fallback
COMMIT_SELECTION = os.getenv("COMMIT_SELECTION", "higher").strip().lower()
if COMMIT_SELECTION not in {"accurate", "fallback", "higher"}:
    COMMIT_SELECTION = "higher"

ENABLE_GRAPHQL_FALLBACK = _env_bool("ENABLE_GRAPHQL_FALLBACK", True)

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

GRAPHQL_CONTRIB_QUERY = """
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


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


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


def _request_json(method, url, token, params=None, payload=None, max_retries=4):
    attempt = 0

    while True:
        try:
            response = requests.request(
                method,
                url,
                headers=_headers(token),
                params=params,
                json=payload,
                timeout=40,
            )
        except RequestException as exc:
            if attempt < max_retries:
                backoff = 2 ** attempt
                print(f"Network error for {url}: {exc}. Retrying in {backoff}s.")
                time.sleep(backoff)
                attempt += 1
                continue
            raise RuntimeError(f"Request failed for {url}: {exc}") from exc

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


def _normalize_emails(emails):
    return {email.strip().lower() for email in emails if email and email.strip()}


def _validate_accounts(accounts):
    placeholder_emails = {"personal@email.com", "work@email.com"}

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
        response, data = _request_json(
            "GET",
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
            repo_payload = _safe_dict(repo)
            full_name = repo_payload.get("full_name")
            if not full_name or full_name in seen:
                continue
            if not INCLUDE_FORKS and repo_payload.get("fork"):
                continue
            if not INCLUDE_ARCHIVED and repo_payload.get("archived"):
                continue

            seen.add(full_name)
            repos.append(repo_payload)

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return repos


def _build_commit_params(page):
    params = {
        "per_page": COMMITS_PER_PAGE,
        "page": page,
        "since": STATS_FROM_ISO,
        "until": STATS_TO_ISO,
    }
    return params


def _matches_account_identity(commit_payload, username, emails):
    username_lower = username.strip().lower()

    commit_meta = _safe_dict(commit_payload.get("commit"))
    author_meta = _safe_dict(commit_meta.get("author"))
    committer_meta = _safe_dict(commit_meta.get("committer"))

    api_author = _safe_dict(commit_payload.get("author"))
    api_committer = _safe_dict(commit_payload.get("committer"))

    author_email = str(author_meta.get("email", "")).strip().lower()
    committer_email = str(committer_meta.get("email", "")).strip().lower()

    author_login = str(api_author.get("login", "")).strip().lower()
    committer_login = str(api_committer.get("login", "")).strip().lower()

    noreply_match = author_email.endswith("@users.noreply.github.com") and username_lower in author_email

    return (
        author_email in emails
        or committer_email in emails
        or author_login == username_lower
        or committer_login == username_lower
        or noreply_match
    )


def count_commits(repo_full_name, token, emails, username):
    commits = 0
    daily_counts = {}
    page = 1

    while True:
        response, data = _request_json(
            "GET",
            f"{GITHUB_API}/repos/{repo_full_name}/commits",
            token,
            params=_build_commit_params(page),
        )

        if response.status_code in {404, 409}:
            return commits, _sorted_counter(daily_counts)

        if not response.ok:
            raise RuntimeError(
                f"Commit fetch failed (HTTP {response.status_code}): {_extract_error_message(data)}"
            )

        if not isinstance(data, list) or not data:
            break

        for commit in data:
            commit_payload = _safe_dict(commit)
            if _matches_account_identity(commit_payload, username, emails):
                commits += 1
                day = _extract_commit_day(commit_payload)
                if day:
                    _bump_counter(daily_counts, day)

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return commits, _sorted_counter(daily_counts)


def _yearly_chunks(start_dt, end_dt):
    chunks = []
    current = start_dt

    while current < end_dt:
        next_year = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
        chunk_end = next_year if next_year < end_dt else end_dt
        chunks.append((_to_iso_z(current), _to_iso_z(chunk_end)))
        current = chunk_end

    return chunks


def fetch_contribution_range(username, token, start_iso, end_iso):
    response, data = _request_json(
        "POST",
        GRAPHQL_API,
        token,
        payload={
            "query": GRAPHQL_CONTRIB_QUERY,
            "variables": {
                "login": username,
                "from": start_iso,
                "to": end_iso,
            },
        },
    )

    if not response.ok:
        raise RuntimeError(
            f"GraphQL fallback failed (HTTP {response.status_code}): {_extract_error_message(data)}"
        )

    if isinstance(data, dict) and data.get("errors"):
        raise RuntimeError(f"GraphQL fallback error: {_extract_error_message(data)}")

    user_payload = _safe_dict(_safe_dict(_safe_dict(data).get("data")).get("user"))
    contributions = _safe_dict(user_payload.get("contributionsCollection"))

    return {
        "commits": int(contributions.get("totalCommitContributions", 0) or 0),
        "prs": int(contributions.get("totalPullRequestContributions", 0) or 0),
        "issues": int(contributions.get("totalIssueContributions", 0) or 0),
        "restricted": int(contributions.get("restrictedContributionsCount", 0) or 0),
    }


def get_dated_contribution_totals(username, token, start_dt, end_dt):
    totals = {"commits": 0, "prs": 0, "issues": 0, "restricted": 0}

    for start_iso, end_iso in _yearly_chunks(start_dt, end_dt):
        chunk = fetch_contribution_range(username, token, start_iso, end_iso)
        for key in totals:
            totals[key] += chunk[key]

    return totals


def _pick_final_commits(accurate_commits, fallback_commits):
    if COMMIT_SELECTION == "accurate":
        return accurate_commits, "accurate_repo_history"

    if COMMIT_SELECTION == "fallback":
        return fallback_commits, "graphql_contributions"

    if fallback_commits >= accurate_commits:
        return fallback_commits, "graphql_contributions"

    return accurate_commits, "accurate_repo_history"


def aggregate():
    totals = {
        "commits": 0,
        "accurate_commits": 0,
        "fallback_commits": 0,
        "prs": 0,
        "issues": 0,
        "restricted": 0,
    }
    breakdown = {}
    per_repo = {}
    repo_errors = {}
    timeline_by_account_day = {}
    timeline_by_account_month = {}
    timeline_by_repo_day = {}
    timeline_combined_day = {}

    _validate_accounts(ACCOUNTS)

    for account in ACCOUNTS:
        username = account["username"]
        token = account["token"]
        emails = set(account["emails"])

        print(f"Scanning repos for {username}...")
        repos = get_repos(token)

        user_accurate_commits = 0
        user_repo_counts = {}
        user_repo_errors = {}
        user_day_counts = {}
        user_repo_day_counts = {}

        for idx, repo in enumerate(repos, start=1):
            full_name = repo["full_name"]
            try:
                commit_count, repo_day_counts = count_commits(full_name, token, emails, username)
                user_accurate_commits += commit_count
                user_repo_counts[full_name] = commit_count
                if repo_day_counts:
                    user_repo_day_counts[full_name] = repo_day_counts
                    _merge_counters(user_day_counts, repo_day_counts)
                print(f"[{idx}/{len(repos)}] {full_name}: {commit_count}")
            except Exception as exc:
                user_repo_errors[full_name] = str(exc)
                print(f"Skip {full_name}: {exc}")

        user_day_counts = _sorted_counter(user_day_counts)
        timeline_by_account_day[username] = user_day_counts
        timeline_by_account_month[username] = _to_monthly_counts(user_day_counts)
        timeline_by_repo_day[username] = user_repo_day_counts
        _merge_counters(timeline_combined_day, user_day_counts)

        fallback = {"commits": 0, "prs": 0, "issues": 0, "restricted": 0}
        fallback_error = None

        if ENABLE_GRAPHQL_FALLBACK:
            try:
                fallback = get_dated_contribution_totals(username, token, STATS_FROM_DT, STATS_TO_DT)
            except Exception as exc:
                fallback_error = str(exc)
                print(f"Fallback failed for {username}: {exc}")

        final_commits, final_source = _pick_final_commits(user_accurate_commits, fallback["commits"])

        breakdown[username] = {
            "commits": final_commits,
            "final_source": final_source,
            "accurate_commits": user_accurate_commits,
            "fallback_commits": fallback["commits"],
            "prs": fallback["prs"],
            "issues": fallback["issues"],
            "restricted": fallback["restricted"],
            "repos_scanned": len(repos),
            "repos_with_errors": len(user_repo_errors),
            "emails": account["emails"],
            "period": {
                "from": STATS_FROM_ISO,
                "to": STATS_TO_ISO,
            },
            "fallback_error": fallback_error,
        }

        per_repo[username] = user_repo_counts
        repo_errors[username] = user_repo_errors

        totals["commits"] += final_commits
        totals["accurate_commits"] += user_accurate_commits
        totals["fallback_commits"] += fallback["commits"]
        totals["prs"] += fallback["prs"]
        totals["issues"] += fallback["issues"]
        totals["restricted"] += fallback["restricted"]

    timeline_combined_day = _sorted_counter(timeline_combined_day)
    timelines = {
        "source": "accurate_repo_history",
        "granularity": "day",
        "combined_by_day": timeline_combined_day,
        "combined_by_month": _to_monthly_counts(timeline_combined_day),
        "by_account_by_day": timeline_by_account_day,
        "by_account_by_month": timeline_by_account_month,
        "by_repo_by_day": timeline_by_repo_day,
    }

    return totals, breakdown, per_repo, repo_errors, timelines


def save_timeline_csvs(timelines):
    daily_path = "stats_timeline_daily.csv"
    monthly_path = "stats_timeline_monthly.csv"

    with open(daily_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "account", "commits"])

        for date, commits in timelines["combined_by_day"].items():
            writer.writerow([date, "__combined__", commits])

        for account in sorted(timelines["by_account_by_day"]):
            for date, commits in timelines["by_account_by_day"][account].items():
                writer.writerow([date, account, commits])

    with open(monthly_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["month", "account", "commits"])

        for month, commits in timelines["combined_by_month"].items():
            writer.writerow([month, "__combined__", commits])

        for account in sorted(timelines["by_account_by_month"]):
            for month, commits in timelines["by_account_by_month"][account].items():
                writer.writerow([month, account, commits])


def save(totals, breakdown, per_repo, repo_errors, timelines):
    payload = {
        "generated_at": _to_iso_z(datetime.now(timezone.utc)),
        "mode": "hybrid_accurate_with_dated_graphql_fallback",
        "period": {
            "from": STATS_FROM_ISO,
            "to": STATS_TO_ISO,
        },
        "selection": {
            "commit_selection": COMMIT_SELECTION,
            "graphql_fallback_enabled": ENABLE_GRAPHQL_FALLBACK,
        },
        "totals": totals,
        "breakdown": breakdown,
        "per_repo": per_repo,
        "repo_errors": repo_errors,
        "timelines": timelines,
    }

    with open("stats.json", "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)

    with open("stats.md", "w", encoding="utf-8") as file:
        file.write("## Combined GitHub Stats (Hybrid Mode)\n\n")

        file.write("### Period\n")
        file.write(f"- from: {STATS_FROM_ISO}\n")
        file.write(f"- to: {STATS_TO_ISO}\n")
        file.write(f"- commit_selection: {COMMIT_SELECTION}\n")

        file.write("\n### Totals\n")
        for key, value in totals.items():
            file.write(f"- {key}: {value}\n")

        file.write("\n### Breakdown\n")
        for username, stats in breakdown.items():
            file.write(f"\n**{username}**\n")
            file.write(f"- commits: {stats['commits']} ({stats['final_source']})\n")
            file.write(f"- accurate_commits: {stats['accurate_commits']}\n")
            file.write(f"- fallback_commits: {stats['fallback_commits']}\n")
            file.write(f"- prs: {stats['prs']}\n")
            file.write(f"- issues: {stats['issues']}\n")
            file.write(f"- restricted: {stats['restricted']}\n")
            file.write(f"- repos_scanned: {stats['repos_scanned']}\n")
            file.write(f"- repos_with_errors: {stats['repos_with_errors']}\n")
            file.write(f"- emails: {', '.join(stats['emails'])}\n")
            if stats["fallback_error"]:
                file.write(f"- fallback_error: {stats['fallback_error']}\n")

        file.write("\n### Timelines\n")
        file.write(f"- source: {timelines['source']}\n")
        file.write(f"- combined_daily_points: {len(timelines['combined_by_day'])}\n")
        file.write(f"- csv_daily: stats_timeline_daily.csv\n")
        file.write(f"- csv_monthly: stats_timeline_monthly.csv\n")

    save_timeline_csvs(timelines)


if __name__ == "__main__":
    totals_result, breakdown_result, per_repo_result, repo_errors_result, timelines_result = aggregate()
    save(totals_result, breakdown_result, per_repo_result, repo_errors_result, timelines_result)
