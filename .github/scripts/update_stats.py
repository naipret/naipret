from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Try importing pyyaml, with clear error message if missing
try:
    import yaml
except ImportError:
    yaml = None


# ------------------------------------------------------------------------------
# Data Models
# ------------------------------------------------------------------------------


@dataclass
class StackItem:
    name: str
    proficiency: str = "-"
    status: str = "-"
    languages: List[str] = field(default_factory=list)


@dataclass
class StackCategory:
    category: str
    items: List[StackItem] = field(default_factory=list)


@dataclass
class MetricItem:
    key: str
    label: str


@dataclass
class ColumnConfig:
    header: str
    metrics: List[MetricItem] = field(default_factory=list)


@dataclass
class OverviewConfig:
    left_column: ColumnConfig
    right_column: ColumnConfig


@dataclass
class AppConfig:
    username: str
    organizations: List[str]
    stack: List[StackCategory]
    overview: OverviewConfig


# ------------------------------------------------------------------------------
# Configuration Manager
# ------------------------------------------------------------------------------


class ConfigManager:
    """Handles loading and parsing configuration from YAML or JSON."""

    @staticmethod
    def find_config_path(base_dir: Path) -> Path:
        candidates = [
            base_dir / ".github" / "config.yml",
            base_dir / ".github" / "config.yaml",
            base_dir / ".github" / "config.json",
            base_dir / "config.yml",
            base_dir / "config.json",
        ]
        for path in candidates:
            if path.is_file():
                return path
        raise FileNotFoundError(
            f"No configuration file found. Checked: {[str(c) for c in candidates]}"
        )

    @classmethod
    def load(cls, base_dir: Path) -> AppConfig:
        config_path = cls.find_config_path(base_dir)
        print(f"Loading configuration from: {config_path}")

        raw_data: Dict[str, Any] = {}
        with open(config_path, "r", encoding="utf-8") as f:
            if config_path.suffix in [".yml", ".yaml"]:
                if yaml is None:
                    raise ImportError(
                        "PyYAML is required to parse YAML config. "
                        "Install it with 'pip install pyyaml'."
                    )
                raw_data = yaml.safe_load(f) or {}
            else:
                raw_data = json.load(f)

        # Parse User
        user_info = raw_data.get("user", {})
        username = user_info.get("username", "naipret")
        organizations = user_info.get("organizations", [])

        # Parse Stack
        stack_categories: List[StackCategory] = []
        for cat_data in raw_data.get("stack", []):
            cat_name = cat_data.get("category", "")
            items: List[StackItem] = []
            for item_data in cat_data.get("items", []):
                items.append(
                    StackItem(
                        name=item_data.get("name", ""),
                        proficiency=item_data.get("proficiency", "-"),
                        status=item_data.get("status", "-"),
                        languages=item_data.get("languages", []) or [],
                    )
                )
            stack_categories.append(StackCategory(category=cat_name, items=items))

        # Parse Overview
        overview_data = raw_data.get("overview", {})
        left_data = overview_data.get("left_column", {})
        right_data = overview_data.get("right_column", {})

        left_metrics = [
            MetricItem(key=m.get("key", ""), label=m.get("label", ""))
            for m in left_data.get("metrics", [])
        ]
        right_metrics = [
            MetricItem(key=m.get("key", ""), label=m.get("label", ""))
            for m in right_data.get("metrics", [])
        ]

        overview_config = OverviewConfig(
            left_column=ColumnConfig(
                header=left_data.get("header", "Activity"),
                metrics=left_metrics,
            ),
            right_column=ColumnConfig(
                header=right_data.get("header", "Community"),
                metrics=right_metrics,
            ),
        )

        return AppConfig(
            username=username,
            organizations=organizations,
            stack=stack_categories,
            overview=overview_config,
        )


# ------------------------------------------------------------------------------
# GitHub API Client
# ------------------------------------------------------------------------------


class GitHubApiClient:
    """Manages all authenticated requests to GitHub REST and GraphQL APIs."""

    def __init__(
        self,
        token: Optional[str] = None,
        user_agent: str = "profile-stats-bot",
    ):
        self.token = token or self._resolve_token()
        self.user_agent = user_agent

    @staticmethod
    def _resolve_token() -> Optional[str]:
        # Check standard environment variables
        for var in ["STATS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"]:
            val = os.environ.get(var)
            if val and val.strip():
                return val.strip()

        # Fallback to local gh CLI if available
        try:
            import subprocess

            proc = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            pass

        return None

    def request(self, url: str, data: Optional[Dict[str, Any]] = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req_body = None
        if data is not None:
            req_body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8").strip()
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as e:
            print(f"[API Error] HTTP {e.code} for URL: {url}", file=sys.stderr)
            if e.code == 403:
                print(
                    "Warning: GitHub API rate limit reached or token lacks permission.",
                    file=sys.stderr,
                )
            return None
        except Exception as e:
            print(f"[API Error] Failed to fetch {url}: {e}", file=sys.stderr)
            return None

    def graphql(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            print(
                "[GraphQL] Warning: No token provided for GraphQL API.", file=sys.stderr
            )
            return {}

        res = self.request(
            "https://api.github.com/graphql",
            data={"query": query, "variables": variables},
        )
        if res and isinstance(res, dict) and "data" in res:
            return res.get("data", {})
        return {}


# ------------------------------------------------------------------------------
# Statistics Aggregator
# ------------------------------------------------------------------------------


class StatsAggregator:
    """Aggregates repositories, commits, language bytes, and community metrics."""

    def __init__(
        self,
        client: GitHubApiClient,
        username: str,
        organizations: List[str],
    ):
        self.client = client
        self.username = username
        self.organizations = organizations

    def fetch_all_repositories(self) -> List[Dict[str, Any]]:
        repos: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()

        # 1. Fetch user owned repos
        user_url = f"https://api.github.com/user/repos?per_page=100&affiliation=owner"
        user_repos = self.client.request(user_url)
        if not isinstance(user_repos, list):
            fallback_url = f"https://api.github.com/users/{self.username}/repos?per_page=100&type=owner"
            user_repos = self.client.request(fallback_url)

        if isinstance(user_repos, list):
            for r in user_repos:
                rid = r.get("id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    repos.append(r)

        # 2. Fetch organization repos
        for org in self.organizations:
            org_url = f"https://api.github.com/orgs/{org}/repos?per_page=100"
            org_repos = self.client.request(org_url)
            if isinstance(org_repos, list):
                for r in org_repos:
                    rid = r.get("id")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        repos.append(r)

        return repos

    def aggregate_language_bytes(self, repos: List[Dict[str, Any]]) -> Dict[str, int]:
        lang_bytes: Dict[str, int] = {}
        non_forks = [r for r in repos if not r.get("fork", False)]
        print(f"Aggregating languages across {len(non_forks)} non-fork repositories...")

        for repo in non_forks:
            lang_url = repo.get("languages_url")
            if not lang_url:
                continue
            res = self.client.request(lang_url)
            if isinstance(res, dict):
                for lang, count in res.items():
                    lang_bytes[lang] = lang_bytes.get(lang, 0) + count

        return lang_bytes

    def fetch_metrics(self, repos: List[Dict[str, Any]]) -> Dict[str, int]:
        # Repository counts
        public_repos = sum(1 for r in repos if not r.get("private", False))
        private_repos = sum(1 for r in repos if r.get("private", False))
        total_stars = sum(r.get("stargazers_count", 0) for r in repos)
        total_forks = sum(r.get("forks_count", 0) for r in repos)

        # GraphQL Collection
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
            sponsorshipsAsMaintainer {
              totalCount
            }
          }
        }
        """
        gql_data = self.client.graphql(query, {"login": self.username})
        user_node = gql_data.get("user") or {}
        contrib = user_node.get("contributionsCollection", {})

        calendar_commits = contrib.get("totalCommitContributions", 0)
        prs_opened = contrib.get("totalPullRequestContributions", 0)
        prs_reviewed = contrib.get("totalPullRequestReviewContributions", 0)
        issues_opened = contrib.get("totalIssueContributions", 0)
        sponsors = user_node.get("sponsorshipsAsMaintainer", {}).get("totalCount", 0)

        # Total commits via search fallback
        total_commits = self._fetch_total_commits_count(fallback=calendar_commits)

        # Issue comments count
        issue_comments = self._fetch_issue_comments_count()

        # Contributors count across repos (excluding current user)
        contributors_count = self.fetch_contributors_count(repos)

        return {
            "commits": total_commits,
            "prs_opened": prs_opened,
            "prs_reviewed": prs_reviewed,
            "issues_opened": issues_opened,
            "issue_comments": issue_comments,
            "sponsors": sponsors,
            "contributors": contributors_count,
            "public_repos": public_repos,
            "private_repos": private_repos,
            "stars": total_stars,
            "forks": total_forks,
        }

    def fetch_contributors_count(
        self,
        repos: List[Dict[str, Any]],
    ) -> int:
        contributors: set[str] = set()
        non_forks = [r for r in repos if not r.get("fork", False)]
        print(f"Counting contributors across {len(non_forks)} non-fork repositories...")

        for repo in non_forks:
            c_url = repo.get("contributors_url")
            if not c_url:
                continue
            res = self.client.request(c_url)
            if isinstance(res, list):
                for contributor in res:
                    login = contributor.get("login")
                    if login and login.lower() != self.username.lower():
                        contributors.add(login)

        return len(contributors)

    def _fetch_total_commits_count(self, fallback: int) -> int:
        url = f"https://api.github.com/search/commits?q=author:{self.username}"
        res = self.client.request(url)
        if isinstance(res, dict) and "total_count" in res:
            return res["total_count"]
        return fallback

    def _fetch_issue_comments_count(self) -> int:
        url = f"https://api.github.com/search/issues?q=commenter:{self.username}"
        res = self.client.request(url)
        if isinstance(res, dict):
            return res.get("total_count", 0)
        return 0


# ------------------------------------------------------------------------------
# Markdown Renderer
# ------------------------------------------------------------------------------


class MarkdownRenderer:
    """Renders clean, GitHub Flavored Markdown (GFM) tables."""

    @staticmethod
    def get_timestamp_note() -> str:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"<sub>*Automated synchronization via custom GitHub Actions workflow (Last updated: {now_utc})*</sub>"

    @classmethod
    def render_stack_table(
        cls,
        categories: List[StackCategory],
        language_bytes: Dict[str, int],
    ) -> str:
        total_bytes = sum(language_bytes.values())

        lines = [
            "| Category | Technologies | Proficiency | Status | Percentage |",
            "| :--- | :--- | :--- | :--- | :---: |",
        ]

        for cat in categories:
            for idx, item in enumerate(cat.items):
                # Category label only appears on the first item in the group
                cat_col = f"**{cat.category}**" if idx == 0 else ""

                pct_col = "-"
                if item.languages and total_bytes > 0:
                    matched_bytes = sum(
                        language_bytes.get(l, 0) for l in item.languages
                    )
                    if matched_bytes > 0:
                        pct = (matched_bytes / total_bytes) * 100
                        pct_col = f"{pct:.1f}%"

                lines.append(
                    f"| {cat_col} | {item.name} | {item.proficiency} | {item.status} | {pct_col} |"
                )

        lines.append("")
        lines.append(cls.get_timestamp_note())
        return "\n".join(lines)

    @classmethod
    def render_overview_table(
        cls,
        config: OverviewConfig,
        metrics_data: Dict[str, int],
    ) -> str:
        left = config.left_column
        right = config.right_column

        left_header = left.header
        right_header = right.header

        lines = [
            f"| {left_header} | Value | {right_header} | Value |",
            "| :--- | :---: | :--- | :---: |",
        ]

        max_rows = max(len(left.metrics), len(right.metrics))
        for i in range(max_rows):
            # Left column item
            if i < len(left.metrics):
                m_left = left.metrics[i]
                val_left = metrics_data.get(m_left.key, 0)
                l_col = f"{m_left.label} | {val_left:,}"
            else:
                l_col = " | "

            # Right column item
            if i < len(right.metrics):
                m_right = right.metrics[i]
                val_right = metrics_data.get(m_right.key, 0)
                r_col = f"{m_right.label} | {val_right:,}"
            else:
                r_col = " | "

            lines.append(f"| {l_col} | {r_col} |")

        lines.append("")
        lines.append(cls.get_timestamp_note())
        return "\n".join(lines)


# ------------------------------------------------------------------------------
# README Synchronizer
# ------------------------------------------------------------------------------


class ReadmeSynchronizer:
    """Updates designated section blocks in the target README.md file."""

    STACK_START = "<!-- START_SECTION:stack -->"
    STACK_END = "<!-- END_SECTION:stack -->"
    STATS_START = "<!-- START_SECTION:stats -->"
    STATS_END = "<!-- END_SECTION:stats -->"

    @classmethod
    def sync(cls, readme_path: Path, stack_md: str, stats_md: str) -> None:
        if not readme_path.is_file():
            raise FileNotFoundError(f"Target README not found at {readme_path}")

        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Update Stack section
        stack_pattern = re.compile(
            rf"({re.escape(cls.STACK_START)})(.*?)({re.escape(cls.STACK_END)})",
            flags=re.DOTALL,
        )
        if stack_pattern.search(content):
            content = stack_pattern.sub(f"\\1\n{stack_md}\n\\3", content)
        else:
            print(
                "[Warning] Stack section markers not found in README.md.",
                file=sys.stderr,
            )

        # 2. Update Stats section
        stats_pattern = re.compile(
            rf"({re.escape(cls.STATS_START)})(.*?)({re.escape(cls.STATS_END)})",
            flags=re.DOTALL,
        )
        if stats_pattern.search(content):
            content = stats_pattern.sub(f"\\1\n{stats_md}\n\\3", content)
        else:
            print(
                "[Warning] Stats section markers not found in README.md.",
                file=sys.stderr,
            )

        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"Successfully synchronized {readme_path}")


# ------------------------------------------------------------------------------
# Main Entry Point
# ------------------------------------------------------------------------------


def main() -> None:
    workspace_dir = Path(__file__).resolve().parent.parent.parent
    readme_path = workspace_dir / "README.md"

    print("=" * 60)
    print("Starting Profile README Synchronization Pipeline")
    print(f"Workspace Directory: {workspace_dir}")
    print("=" * 60)

    # 1. Load configuration
    config = ConfigManager.load(workspace_dir)

    # 2. Initialize API client
    api_client = GitHubApiClient(user_agent=f"{config.username}-stats-bot")

    # 3. Aggregate statistics
    aggregator = StatsAggregator(
        client=api_client,
        username=config.username,
        organizations=config.organizations,
    )

    repos = aggregator.fetch_all_repositories()
    print(f"Discovered {len(repos)} repositories (including organizations).")

    lang_bytes = aggregator.aggregate_language_bytes(repos)
    metrics_data = aggregator.fetch_metrics(repos)

    # 4. Render Markdown
    stack_markdown = MarkdownRenderer.render_stack_table(config.stack, lang_bytes)
    stats_markdown = MarkdownRenderer.render_overview_table(
        config.overview, metrics_data
    )

    # 5. Synchronize README
    ReadmeSynchronizer.sync(readme_path, stack_markdown, stats_markdown)
    print("Pipeline execution completed successfully!")


if __name__ == "__main__":
    main()
