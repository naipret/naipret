import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

USERNAME = "naipret"
README_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
START_MARKER = "<!-- START_SECTION:stats -->"
END_MARKER = "<!-- END_SECTION:stats -->"
BAR_WIDTH = 20


def make_github_request(url: str, token: str | None = None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-stats-bot",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
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


def format_bytes(b: int) -> str:
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    elif b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def render_progress_bar(percentage: float, width: int = BAR_WIDTH) -> str:
    filled_len = int(round(width * percentage / 100.0))
    filled_len = max(0, min(width, filled_len))
    return "█" * filled_len + "░" * (width - filled_len)


def generate_stats_block(repos: list, token: str | None) -> str:
    public_repos = [r for r in repos if not r.get("private", False) and not r.get("fork", False)]
    total_public = len(public_repos)
    total_stars = sum(r.get("stargazers_count", 0) for r in public_repos)
    total_forks = sum(r.get("forks_count", 0) for r in public_repos)

    language_bytes = {}

    print(f"Fetching language statistics for {total_public} public repositories...")
    for repo in public_repos:
        repo_name = repo["name"]
        lang_url = repo.get("languages_url")
        if not lang_url:
            continue
        langs = make_github_request(lang_url, token)
        if isinstance(langs, dict):
            for lang, count in langs.items():
                language_bytes[lang] = language_bytes.get(lang, 0) + count

    total_bytes = sum(language_bytes.values())
    sorted_langs = sorted(language_bytes.items(), key=lambda x: x[1], reverse=True)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("| Metric | Value |")
    lines.append("| :--- | :---: |")
    lines.append(f"| **Public Repositories** | {total_public} |")
    lines.append(f"| **Community Stars** | {total_stars} |")
    lines.append(f"| **Community Forks** | {total_forks} |")
    lines.append("")
    lines.append("### Top Languages")
    lines.append("")
    lines.append("| Language | Distribution | Percentage |")
    lines.append("| :--- | :--- | :---: |")

    # Top 10 languages or all if less
    top_langs = sorted_langs[:10]
    for lang, b in top_langs:
        pct = (b / total_bytes * 100) if total_bytes > 0 else 0.0
        bar = render_progress_bar(pct, BAR_WIDTH)
        lines.append(f"| **{lang}** | `{bar}` | {pct:.1f}% |")

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
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    # If running locally and gh CLI is available, try getting token from gh auth token
    if not token:
        try:
            import subprocess
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                token = res.stdout.strip()
        except Exception:
            pass

    repos_url = f"https://api.github.com/users/{USERNAME}/repos?per_page=100&type=owner"
    print(f"Fetching repositories from {repos_url}...")
    repos = make_github_request(repos_url, token)

    if not isinstance(repos, list):
        print("Failed to fetch repository list.", file=sys.stderr)
        sys.exit(1)

    stats_content = generate_stats_block(repos, token)
    update_readme(stats_content)


if __name__ == "__main__":
    main()
