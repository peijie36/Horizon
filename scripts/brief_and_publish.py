"""Generate a local Horizon briefing and publish its Markdown to GitHub Pages."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://peijie36.github.io/Horizon/"
REMOTE_PATTERN = re.compile(
    r"(?:https://github\.com/|git@github\.com:)peijie36/Horizon(?:\.git)?/?$",
    re.IGNORECASE,
)


def git(root: Path, *arguments: str, capture: bool = False, check: bool = True):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=check,
        text=True,
        capture_output=capture,
    )


def brief_paths(root: Path) -> list[str]:
    paths = []
    for folder, pattern in [
        ("docs/_posts", r"\d{4}-\d{2}-\d{2}-summary-(?:en|zh)\.md"),
        ("data/summaries", r"horizon-\d{4}-\d{2}-\d{2}-(?:en|zh)\.md"),
    ]:
        for path in sorted((root / folder).glob("*.md")):
            if re.fullmatch(pattern, path.name):
                paths.append(f"{folder}/{path.name}")
    return paths


def publish(root: Path, hours: int, publish_only: bool, dry_run: bool) -> None:
    if dry_run:
        if not publish_only:
            print(f"Would generate a local briefing covering {hours} hours.")
        print("Would commit only generated briefing Markdown and push to peijie36/Horizon.")
        print(f"GitHub Actions would rebuild {SITE_URL}")
        return

    branch = git(root, "branch", "--show-current", capture=True).stdout.strip()
    if branch != "main":
        raise RuntimeError("Run this command from main. No branch was switched.")

    remote = git(root, "remote", "get-url", "origin", capture=True).stdout.strip()
    if not REMOTE_PATTERN.fullmatch(remote):
        raise RuntimeError("Origin must point to your peijie36/Horizon repository.")

    git(root, "fetch", "--quiet", "origin", "main")
    behind = int(git(root, "rev-list", "--count", "HEAD..origin/main", capture=True).stdout)
    ahead = int(git(root, "rev-list", "--count", "origin/main..HEAD", capture=True).stdout)
    if behind:
        raise RuntimeError("Main is behind GitHub. Run git pull --ff-only before generating.")
    if ahead:
        raise RuntimeError("Review and push your existing local commits first; they were not published.")

    if not publish_only:
        if not (root / "data/config.json").is_file():
            raise RuntimeError("Missing data/config.json. Complete local Horizon setup first.")
        subprocess.run(
            [sys.executable, "-m", "src.main", "--config", "data/config.json", "--hours", str(hours)],
            cwd=root,
            check=True,
        )

    paths = brief_paths(root)
    if not paths:
        raise RuntimeError("No generated briefing Markdown was found. Nothing was published.")

    git(root, "add", "--force", "--", *paths)
    changed = git(root, "diff", "--cached", "--quiet", "--", *paths, check=False).returncode
    if changed not in (0, 1):
        raise RuntimeError("Could not inspect staged briefing changes.")
    if changed:
        # --only keeps unrelated staged work out of this publication commit.
        git(root, "commit", "--only", "-m", "Publish Horizon briefing", "--", *paths)
        git(root, "push", "origin", "HEAD:main")
        print("Briefing pushed. GitHub Pages will update after the HTML build completes.")
    else:
        print("The generated briefings already match GitHub.")
    print(SITE_URL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24, help="Collection window (default: 24).")
    parser.add_argument("--publish-only", action="store_true", help="Publish existing briefs without AI calls.")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan without generation or Git writes.")
    args = parser.parse_args()
    if not 1 <= args.hours <= 168:
        parser.error("--hours must be between 1 and 168")
    try:
        publish(ROOT, args.hours, args.publish_only, args.dry_run)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, subprocess.CalledProcessError):
            message = "A generation or Git command failed. Local files were preserved."
        else:
            message = str(exc)
        print(f"Publication stopped: {message}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
