import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

USERNAME = "naipret"
ORGANIZATIONS = ["naf-studio"]
README_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "README.md")
START_MARKER = "<!-- START_SECTION:stats -->"
END_MARKER = "<!-- END_SECTION:stats -->"
BAR_WIDTH = 20


def make_github_request(url: str, token: str | None = None, data: dict | None = None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-stats-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req_data = None
    if data is not None:
        req_data = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=req_data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code} for URL: {url}", file=sys.stderr)
        if e.code == 403:
            print("Rate limit exceeded or insufficient permissions.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def fetch_graphql_data(token: str | None) -> dict:
    if not token:
        return {}

    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          totalCommitContributions
          totalPullRequestContributions
          totalPullRequestReviewContributions
          totalIssueContributions
          contributionCalendar {
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
        organizations(first: 10) {
          nodes {
            login
          }
        }
        sponsorshipsAsMaintainer {
          totalCount
        }
      }
    }
    """
    res = make_github_request("https://api.github.com/graphql", token, {"query": query, "variables": {"login": USERNAME}})
    if res and "data" in res and res["data"].get("user"):
        return res["data"]["user"]
    return {}


def calculate_commit_streaks(weeks: list) -> tuple[int, int]:
    # Flatten days sorted by date
    days = []
    for w in weeks:
        for d in w.get("contributionDays", []):
            days.append((d.get("date"), d.get("contributionCount", 0)))

    days.sort(key=lambda x: x[0])

    best_streak = 0
    current_streak = 0
    temp_streak = 0

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for date, count in days:
        if count > 0:
            temp_streak += 1
            if temp_streak > best_streak:
                best_streak = temp_streak
        else:
            temp_streak = 0

    # Calculate current streak ending today or yesterday
    if days:
        active_days = {d: c for d, c in days}
        # Check from last day backwards
        curr = 0
        # iterate in reverse
        reversed_days = list(reversed(days))
        # if today has 0, streak might still be alive if yesterday had > 0
        first = reversed_days[0]
        start_idx = 0
        if first[0] == today_str and first[1] == 0:
            start_idx = 1  # check starting from yesterday

        for i in range(start_idx, len(reversed_days)):
            if reversed_days[i][1] > 0:
                curr += 1
            else:
                break
        current_streak = curr

    return current_streak, best_streak


def fetch_issue_comments_count(token: str | None) -> int:
    url = f"https://api.github.com/search/issues?q=commenter:{USERNAME}"
    res = make_github_request(url, token)
    if res and isinstance(res, dict):
        return res.get("total_count", 0)
    return 0


def fetch_total_commits(token: str | None, fallback_count: int) -> int:
    url = f"https://api.github.com/search/commits?q=author:{USERNAME}"
    res = make_github_request(url, token)
    if res and isinstance(res, dict) and "total_count" in res:
        return res["total_count"]
    return fallback_count


def render_progress_bar(percentage: float, width: int = BAR_WIDTH) -> str:
    filled_len = int(round(width * percentage / 100.0))
    filled_len = max(0, min(width, filled_len))
    return "█" * filled_len + "░" * (width - filled_len)


def generate_stats_block(repos: list, token: str | None) -> str:
    # Repositories counts
    public_repos = [r for r in repos if not r.get("private", False)]
    private_repos = [r for r in repos if r.get("private", False)]

    public_count = len(public_repos)
    private_count = len(private_repos)
    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    total_forks = sum(r.get("forks_count", 0) for r in repos)

    # GraphQL data
    graphql_data = fetch_graphql_data(token)
    contrib = graphql_data.get("contributionsCollection", {})

    commits_calendar_count = contrib.get("totalCommitContributions", 0)
    total_commits = fetch_total_commits(token, commits_calendar_count)
    prs_opened = contrib.get("totalPullRequestContributions", 0)
    prs_reviewed = contrib.get("totalPullRequestReviewContributions", 0)
    issues_opened = contrib.get("totalIssueContributions", 0)

    # Issue comments
    issue_comments = fetch_issue_comments_count(token)

    # Commit streaks
    weeks = contrib.get("contributionCalendar", {}).get("weeks", [])
    current_streak, best_streak = calculate_commit_streaks(weeks)

    # Organizations
    org_nodes = graphql_data.get("organizations", {}).get("nodes", [])
    found_orgs = [o.get("login") for o in org_nodes if o.get("login")]
    for org in ORGANIZATIONS:
        if org not in found_orgs:
            found_orgs.append(org)
    orgs_str = ", ".join(found_orgs) if found_orgs else "None"

    # Sponsors
    sponsors_count = graphql_data.get("sponsorshipsAsMaintainer", {}).get("totalCount", 0)

    # Languages computation
    language_bytes = {}
    print(f"Fetching language statistics for {len(repos)} repositories...")
    for repo in repos:
        # Avoid forks for personal language statistics
        if repo.get("fork", False):
            continue
        lang_url = repo.get("languages_url")
        if not lang_url:
            continue
        langs = make_github_request(lang_url, token)
        if isinstance(langs, dict):
            for lang, count in langs.items():
                language_bytes[lang] = language_bytes.get(lang, 0) + count

    total_bytes = sum(language_bytes.values())
    sorted_langs = sorted(language_bytes.items(), key=lambda x: x[1], reverse=True)

    # Top 10 languages
    top_langs = sorted_langs[:10]
    max_lang_len = max((len(lang) for lang, _ in top_langs), default=10)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("### Activity")
    lines.append(f"- Commits: {total_commits:,}")
    lines.append(f"- Pull requests opened: {prs_opened:,}")
    lines.append(f"- Pull requests reviewed: {prs_reviewed:,}")
    lines.append(f"- Issues opened: {issues_opened:,}")
    lines.append(f"- Issue comments: {issue_comments:,}")
    lines.append(f"- Current commit streak: {current_streak} days")
    lines.append(f"- Best commit streak: {best_streak} days")
    lines.append("")
    lines.append("### Repositories & Community")
    lines.append(f"- Organizations: {orgs_str}")
    lines.append(f"- Public repositories: {public_count}")
    lines.append(f"- Private repositories: {private_count}")
    lines.append(f"- Community stars: {total_stars}")
    lines.append(f"- Community forks: {total_forks}")
    lines.append(f"- Sponsors: {sponsors_count}")
    lines.append("")
    lines.append("### Top Languages")
    for lang, b in top_langs:
        pct = (b / total_bytes * 100) if total_bytes > 0 else 0.0
        bar = render_progress_bar(pct, BAR_WIDTH)
        # Tabbed into 3 columns: Name, bar, percentage
        lang_padded = f"{lang}:".ljust(max_lang_len + 2)
        lines.append(f"- {lang_padded} `{bar}`   {pct:5.1f}%")

    lines.append("")
    lines.append(f"<sub>*Automated synchronization via custom GitHub Actions workflow (Last updated: {now_utc})*</sub>")

    return "\n".join(lines)


def update_readme(new_content: str):
    if not os.path.exists(README_PATH):
        print(f"Error: {README_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    pattern = re.compile(
        rf"({re.escape(START_MARKER)})(.*?)({re.escape(END_MARKER)})",
        flags=re.DOTALL
    )

    if not pattern.search(readme):
        print(f"Error: Markers '{START_MARKER}' and '{END_MARKER}' not found in README.md.", file=sys.stderr)
        sys.exit(1)

    replacement = f"{START_MARKER}\n{new_content}\n{END_MARKER}"
    updated_readme = pattern.sub(replacement, readme)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(updated_readme)

    print("README.md updated successfully!")


def main():
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    # If running locally and gh CLI is available, try getting token from gh auth token
    if not token:
        try:
            import subprocess
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
        except Exception:
            pass

    repos = []
    seen_repo_ids = set()

    # 1. Fetch user repos (with affiliation=owner to get public and private if token has permission)
    user_repos_url = "https://api.github.com/user/repos?per_page=100&affiliation=owner"
    print(f"Fetching authenticated user repositories from {user_repos_url}...")
    user_repos = make_github_request(user_repos_url, token)

    if not isinstance(user_repos, list):
        # Fallback to public user repos endpoint if not authenticated as user
        fallback_url = f"https://api.github.com/users/{USERNAME}/repos?per_page=100&type=owner"
        print(f"Fallback to {fallback_url}...")
        user_repos = make_github_request(fallback_url, token)

    if isinstance(user_repos, list):
        for r in user_repos:
            repo_id = r.get("id")
            if repo_id and repo_id not in seen_repo_ids:
                seen_repo_ids.add(repo_id)
                repos.append(r)
    else:
        print(f"Warning: Failed to fetch repositories for user {USERNAME}.", file=sys.stderr)

    # 2. Fetch org repos
    for org in ORGANIZATIONS:
        org_repos_url = f"https://api.github.com/orgs/{org}/repos?per_page=100"
        print(f"Fetching organization repositories from {org_repos_url}...")
        org_repos = make_github_request(org_repos_url, token)
        if isinstance(org_repos, list):
            for r in org_repos:
                repo_id = r.get("id")
                if repo_id and repo_id not in seen_repo_ids:
                    seen_repo_ids.add(repo_id)
                    repos.append(r)
        else:
            print(f"Warning: Failed to fetch repositories for org {org}.", file=sys.stderr)

    if not repos:
        print("Failed to fetch any repositories.", file=sys.stderr)
        sys.exit(1)

    stats_content = generate_stats_block(repos, token)
    update_readme(stats_content)


if __name__ == "__main__":
    main()
