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
STACK_START_MARKER = "<!-- START_SECTION:stack -->"
STACK_END_MARKER = "<!-- END_SECTION:stack -->"
BAR_WIDTH = 12

# Mapping from Stack Technology name to GitHub Language name(s)
TECH_LANG_MAP = {
    "C": ["C"],
    "C++": ["C++"],
    "Java": ["Java"],
    "Python": ["Python"],
    "JavaScript": ["JavaScript"],
    "TypeScript": ["TypeScript"],
    "HTML": ["HTML"],
    "CSS": ["CSS"],
    "Tailwind": ["CSS"],
    "LaTeX": ["TeX", "LaTeX"],
    "GNU Make": ["Makefile"],
    "CMake": ["CMake"],
    "Maven": ["Maven POM", "XML"],
    "Docker": ["Dockerfile"],
    "Shell": ["Shell"],
    "Batchfile": ["Batchfile"],
}


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
    days = []
    for w in weeks:
        for d in w.get("contributionDays", []):
            days.append((d.get("date"), d.get("contributionCount", 0)))

    days.sort(key=lambda x: x[0])

    best_streak = 0
    temp_streak = 0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for date, count in days:
        if count > 0:
            temp_streak += 1
            if temp_streak > best_streak:
                best_streak = temp_streak
        else:
            temp_streak = 0

    current_streak = 0
    if days:
        reversed_days = list(reversed(days))
        first = reversed_days[0]
        start_idx = 0
        if first[0] == today_str and first[1] == 0:
            start_idx = 1

        for i in range(start_idx, len(reversed_days)):
            if reversed_days[i][1] > 0:
                current_streak += 1
            else:
                break

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
    if percentage <= 0:
        return "░" * width
    filled_len = int(round(width * percentage / 100.0))
    filled_len = max(1 if percentage > 0.05 else 0, min(width, filled_len))
    return "█" * filled_len + "░" * (width - filled_len)


def generate_stats_block(repos: list, token: str | None) -> str:
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

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "<table>",
        "<tr>",
        '<td width="50%" valign="top">',
        "",
        "### Activity",
        "",
        "| Metric | Value |",
        "| :--- | :---: |",
        f"| Commits | {total_commits:,} |",
        f"| Pull requests opened | {prs_opened:,} |",
        f"| Pull requests reviewed | {prs_reviewed:,} |",
        f"| Issues opened | {issues_opened:,} |",
        f"| Issue comments | {issue_comments:,} |",
        "",
        "</td>",
        '<td width="50%" valign="top">',
        "",
        "### Repositories & Community",
        "",
        "| Metric | Value |",
        "| :--- | :---: |",
        f"| Organizations | {orgs_str} |",
        f"| Public repositories | {public_count} |",
        f"| Private repositories | {private_count} |",
        f"| Community stars | {total_stars} |",
        f"| Community forks | {total_forks} |",
        "",
        "</td>",
        "</tr>",
        "</table>",
        "",
        f"<sub>*Automated synchronization via custom GitHub Actions workflow (Last updated: {now_utc})*</sub>",
    ]

    return "\n".join(lines)


def fetch_language_bytes(repos: list, token: str | None) -> dict[str, int]:
    language_bytes = {}
    print(f"Fetching language statistics for {len(repos)} repositories...")
    for repo in repos:
        if repo.get("fork", False):
            continue
        lang_url = repo.get("languages_url")
        if not lang_url:
            continue
        langs = make_github_request(lang_url, token)
        if isinstance(langs, dict):
            for lang, count in langs.items():
                language_bytes[lang] = language_bytes.get(lang, 0) + count
    return language_bytes


def update_stack_table(readme: str, language_bytes: dict[str, int]) -> str:
    total_bytes = sum(language_bytes.values())

    # Find the Stack table. We support either explicit markers or auto-detecting the table under ## Stack
    stack_pattern = re.compile(
        rf"({re.escape(STACK_START_MARKER)})(.*?)({re.escape(STACK_END_MARKER)})",
        flags=re.DOTALL
    )

    has_markers = bool(stack_pattern.search(readme))

    if not has_markers:
        # Detect table under ## Stack
        table_match = re.search(r"(## Stack\s*\n\s*)(\| Category \| Technologies \|[^\n]+\n\|[ :|-]+\n(?:\|[^\n]+\n)+)", readme)
        if not table_match:
            print("Warning: Stack table not found in README.md.", file=sys.stderr)
            return readme
        original_table = table_match.group(2)
    else:
        original_table = stack_pattern.search(readme).group(2).strip()

    # Base rows definition
    base_rows = [
        ("Programming Language", "C", "Intermediate", "Deepening"),
        ("", "C++", "Intermediate", "Deepening"),
        ("", "Java", "Beginner", "Learning"),
        ("", "Python", "Intermediate", "Deepening"),
        ("", "JavaScript", "Beginner", "Learning"),
        ("", "TypeScript", "-", "Planned"),
        ("Frontend", "HTML", "Intermediate", "Deepening"),
        ("", "CSS", "Beginner", "Learning"),
        ("", "Tailwind", "-", "Planned"),
        ("Backend", "Spring Boot", "Beginner", "Learning"),
        ("Databases", "-", "Beginner", "Learning"),
        ("DevOps", "Linux", "Intermediate", "Deepening"),
        ("", "Git", "Intermediate", "-"),
        ("", "GitHub", "Intermediate", "-"),
        ("", "GitHub Actions", "Beginner", "Learning"),
        ("", "Docker", "Beginner", "Learning"),
        ("", "Kubernetes", "-", "Planned"),
        ("Build", "GNU Make", "Intermediate", "-"),
        ("", "CMake", "Intermediate", "-"),
        ("", "Maven", "Beginner", "Learning"),
        ("", "Gradle", "-", "Planned"),
        ("Research", "LaTeX", "Beginner", "Learning"),
    ]

    new_table_lines = [
        "| Category | Technologies | Proficiency | Status | Distribution | Percentage |",
        "| :--- | :--- | :--- | :--- | :--- | :---: |",
    ]

    for cat, tech, prof, stat in base_rows:
        cat_col = f"**{cat}**" if cat else ""
        mapped_langs = TECH_LANG_MAP.get(tech)

        if mapped_langs and total_bytes > 0:
            tech_bytes = sum(language_bytes.get(l, 0) for l in mapped_langs)
            pct = (tech_bytes / total_bytes * 100) if tech_bytes > 0 else 0.0
            if pct > 0:
                bar = render_progress_bar(pct, BAR_WIDTH)
                dist_col = f"`{bar}`"
                pct_col = f"{pct:.1f}%"
            else:
                dist_col = "-"
                pct_col = "-"
        else:
            dist_col = "-"
            pct_col = "-"

        new_table_lines.append(f"| {cat_col} | {tech} | {prof} | {stat} | {dist_col} | {pct_col} |")

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_table_lines.append("")
    new_table_lines.append(f"<sub>*Automated synchronization via custom GitHub Actions workflow (Last updated: {now_utc})*</sub>")

    new_table_str = "\n".join(new_table_lines)

    if has_markers:
        replacement = f"{STACK_START_MARKER}\n{new_table_str}\n{STACK_END_MARKER}"
        return stack_pattern.sub(replacement, readme)
    else:
        # Wrap table with markers so future updates are clean
        replacement = f"\\1{STACK_START_MARKER}\n{new_table_str}\n{STACK_END_MARKER}"
        return re.sub(
            r"(## Stack\s*\n\s*)(\| Category \| Technologies \|[^\n]+\n\|[ :|-]+\n(?:\|[^\n]+\n)+)",
            replacement,
            readme,
            count=1
        )


def update_readme(stats_content: str, language_bytes: dict[str, int]):
    if not os.path.exists(README_PATH):
        print(f"Error: {README_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    # 1. Update Stack table with Distribution & Percentage
    readme = update_stack_table(readme, language_bytes)

    # 2. Update Stats overview section
    pattern = re.compile(
        rf"({re.escape(START_MARKER)})(.*?)({re.escape(END_MARKER)})",
        flags=re.DOTALL
    )

    if not pattern.search(readme):
        print(f"Error: Markers '{START_MARKER}' and '{END_MARKER}' not found in README.md.", file=sys.stderr)
        sys.exit(1)

    replacement = f"{START_MARKER}\n{stats_content}\n{END_MARKER}"
    updated_readme = pattern.sub(replacement, readme)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(updated_readme)

    print("README.md updated successfully!")


def main():
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

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

    # 1. Fetch user repos
    user_repos_url = "https://api.github.com/user/repos?per_page=100&affiliation=owner"
    print(f"Fetching authenticated user repositories from {user_repos_url}...")
    user_repos = make_github_request(user_repos_url, token)

    if not isinstance(user_repos, list):
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

    language_bytes = fetch_language_bytes(repos, token)
    stats_content = generate_stats_block(repos, token)
    update_readme(stats_content, language_bytes)


if __name__ == "__main__":
    main()
