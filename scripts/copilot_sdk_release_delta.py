#!/usr/bin/env python3
"""Fetch incremental GitHub Copilot SDK releases and generate dated changelog reports."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_REPO = "github/copilot-sdk"
DEFAULT_OUTPUT_DIR = Path("changelog/copilot-sdk")
RELEASES_WEB_URL = "https://github.com/github/copilot-sdk/releases"

SUGGESTION_RULES: list[tuple[list[str], str]] = [
    (
        ["session", "conversation", "context"],
        "將新的 session/context API 暴露到 Telegram 指令，並補齊 list/delete/switch 流程的整合測試。",
    ),
    (
        ["model", "reasoning", "effort", "temperature"],
        "把新的 model/reasoning 控制項映射到 runtime 設定與 `/copilot` 診斷輸出。",
    ),
    (
        ["mcp", "tool", "server"],
        "盤點 MCP/tooling 變更，預設啟用前先加上 feature flag。",
    ),
    (
        ["auth", "token", "login", "status"],
        "補上健康度/授權可視化指令路徑，讓維運能及早發現 auth 漂移。",
    ),
    (
        ["event", "stream", "callback", "subscription"],
        "驗證 event-stream 在高負載下的行為，並在測試中保留 listener 清理斷言。",
    ),
    (
        ["retry", "timeout", "stability", "reliability", "error"],
        "更新可靠性封裝（retry/timeout/error mapping），與 SDK 版本行為對齊。",
    ),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track incremental GitHub Copilot SDK releases and write a dated changelog report."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in owner/name format")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for dated report files (default: changelog/copilot-sdk)",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="State JSON path (default: <output-dir>/.state.json)",
    )
    parser.add_argument(
        "--bootstrap-count",
        type=int,
        default=8,
        help="How many latest releases to include on first run or state miss",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="How many release API pages to fetch (100 per page)",
    )
    parser.add_argument(
        "--api-base",
        default="https://api.github.com",
        help="GitHub API base URL",
    )
    parser.add_argument(
        "--include-prerelease",
        action="store_true",
        default=True,
        help="Include prereleases (enabled by default)",
    )
    parser.add_argument(
        "--exclude-prerelease",
        action="store_true",
        help="Exclude prereleases from incremental report",
    )
    return parser.parse_args()


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "claude-code-telegram/copilot-release-delta",
    }
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_releases(repo: str, api_base: str, max_pages: int) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        url = f"{api_base}/repos/{repo}/releases?{query}"
        req = urllib.request.Request(url, headers=github_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API network error: {exc}") from exc

        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected API response type: {type(payload)!r}")

        if not payload:
            break

        releases.extend(payload)
        if len(payload) < 100:
            break

    return releases


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, repo: str, latest_release: dict[str, Any]) -> None:
    state = {
        "repo": repo,
        "last_seen_release_id": latest_release.get("id"),
        "last_seen_tag": latest_release.get("tag_name"),
        "last_seen_published_at": latest_release.get("published_at"),
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(state, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def select_incremental(
    releases: list[dict[str, Any]], last_seen_release_id: Any, bootstrap_count: int
) -> tuple[list[dict[str, Any]], str]:
    if not releases:
        return [], "empty"

    if last_seen_release_id is None:
        return releases[:bootstrap_count], "bootstrap"

    for idx, rel in enumerate(releases):
        if rel.get("id") == last_seen_release_id:
            return releases[:idx], "incremental"

    return releases[:bootstrap_count], "state-miss"


def normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n")
    text = re.sub(r"`", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_highlights(body: str, max_items: int = 5) -> list[str]:
    highlights: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith(("-", "*")):
            item = normalize_text(s.lstrip("-* "))
            if item and len(item) >= 8:
                highlights.append(item)
        if len(highlights) >= max_items:
            break

    if highlights:
        return highlights

    for line in body.splitlines():
        s = normalize_text(line)
        if len(s) >= 20:
            highlights.append(s)
        if len(highlights) >= max_items:
            break

    return highlights


def release_suggestions(release: dict[str, Any]) -> list[str]:
    combined = " ".join(
        [
            str(release.get("tag_name") or ""),
            str(release.get("name") or ""),
            str(release.get("body") or ""),
        ]
    ).lower()
    suggestions: list[str] = []
    for keywords, suggestion in SUGGESTION_RULES:
        if any(keyword in combined for keyword in keywords):
            suggestions.append(suggestion)

    if release.get("prerelease"):
        suggestions.append(
            "對 prerelease 功能採用 opt-in 旗標，待 stable 版確認 API 行為後再預設開啟。"
        )

    if not suggestions:
        suggestions.append(
            "請人工檢視此版本，決定是否需要更新指令面、設定面與測試面。"
        )

    return suggestions


def aggregate_suggestions(incremental: list[dict[str, Any]]) -> dict[str, list[str]]:
    suggestion_to_tags: dict[str, list[str]] = {}
    for rel in incremental:
        tag = str(rel.get("tag_name") or "unknown-tag")
        for suggestion in release_suggestions(rel):
            suggestion_to_tags.setdefault(suggestion, []).append(tag)
    return suggestion_to_tags


def format_date(iso_value: str | None) -> str:
    if not iso_value:
        return "未知"
    try:
        parsed = dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
    except ValueError:
        return iso_value
    return parsed.strftime("%Y-%m-%d")


def write_report(
    path: Path,
    repo: str,
    incremental: list[dict[str, Any]],
    mode: str,
    last_seen_tag: str | None,
) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    lines: list[str] = []
    lines.append(f"# Copilot SDK 增量更新報告 ({now.strftime('%Y-%m-%d %H:%M:%SZ')})")
    lines.append("")
    lines.append(f"- 來源: {RELEASES_WEB_URL}")
    lines.append(f"- Repo: `{repo}`")
    lines.append(f"- 比對模式: `{mode}`")
    lines.append(f"- 前次標記: `{last_seen_tag or '無'}`")
    lines.append(f"- 本次新增版本數: **{len(incremental)}**")
    lines.append("")

    if not incremental:
        lines.append("相較前次標記，沒有新增 release。")
        lines.append("")
    else:
        lines.append("## 新增 Releases")
        lines.append("")
        for rel in incremental:
            tag = str(rel.get("tag_name") or "unknown")
            name = str(rel.get("name") or "")
            published = format_date(rel.get("published_at"))
            url = str(rel.get("html_url") or "")
            prerelease = "是" if rel.get("prerelease") else "否"
            lines.append(f"### {tag} ({published})")
            if name:
                lines.append(f"- 名稱: {name}")
            lines.append(f"- Pre-release: {prerelease}")
            if url:
                lines.append(f"- 連結: {url}")

            highlights = extract_highlights(str(rel.get("body") or ""))
            if highlights:
                lines.append("- 重點摘要:")
                for item in highlights:
                    lines.append(f"  - {item}")

            suggestions = release_suggestions(rel)
            lines.append("- 整合建議:")
            for item in suggestions:
                lines.append(f"  - {item}")
            lines.append("")

        lines.append("## 整合待辦彙總")
        lines.append("")
        for suggestion, tags in aggregate_suggestions(incremental).items():
            uniq_tags = ", ".join(sorted(set(tags)))
            lines.append(f"- [ ] {suggestion}  (來源版本: {uniq_tags})")
        lines.append("")

        lines.append("## 建議後續流程")
        lines.append("")
        lines.append("1. 先針對優先度最高的 1-2 項待辦建立 OpenSpec change。")
        lines.append("2. 對 SDK 行為仍不確定的功能先以 config flag 方式導入。")
        lines.append("3. 補齊或調整測試後，再評估是否預設啟用到正式環境。")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.bootstrap_count <= 0:
        print("--bootstrap-count must be > 0", file=sys.stderr)
        return 2
    if args.max_pages <= 0:
        print("--max-pages must be > 0", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_file = Path(args.state_file) if args.state_file else output_dir / ".state.json"

    state = load_state(state_file)
    last_seen_release_id = state.get("last_seen_release_id")
    last_seen_tag = state.get("last_seen_tag")

    try:
        releases = fetch_releases(args.repo, args.api_base.rstrip("/"), args.max_pages)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.exclude_prerelease:
        releases = [r for r in releases if not r.get("prerelease")]

    incremental, mode = select_incremental(releases, last_seen_release_id, args.bootstrap_count)

    if incremental:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        report_file = output_dir / f"{timestamp}.md"
        write_report(report_file, args.repo, incremental, mode, last_seen_tag)
        print(f"Report written: {report_file}")
        print(f"New releases in report: {len(incremental)}")
    else:
        print("No new releases found since the previous marker.")

    if releases:
        save_state(state_file, args.repo, releases[0])
        print(f"State updated: {state_file}")
    else:
        print("No releases returned by API; state not updated.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
