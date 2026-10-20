# Publish local briefings to GitHub Pages

Website: https://peijie36.github.io/Horizon/

From a terminal in your Horizon project, generate and publish with:

~~~powershell
uv run python scripts/brief_and_publish.py
~~~

This uses your local data/config.json, profiles, and API environment. It commits
only generated briefing Markdown and pushes to your own main branch. GitHub
Actions builds the HTML and updates the website. Your computer must be on while
the local command runs. There is no scheduled generation or AI use on GitHub.

To publish a brief you already generated without another AI request:

~~~powershell
uv run python scripts/brief_and_publish.py --publish-only
~~~

Use --dry-run to preview either command without generating or publishing.
The script stops before generation if your branch is not main, your remote is
unexpected, or local main is ahead of or behind origin/main. It never switches
branches, resets, rebases, or includes unrelated staged files in its commit.

A regular uv run horizon still generates local files only. Use the publishing
command above, or run --publish-only afterward, to update the hosted page.

The active workflow is .github/workflows/deploy-docs.yml. It only builds and
deploys docs after a push or a manual workflow dispatch. Settings > Pages >
Source must be GitHub Actions. No API secret is needed by this publishing workflow.

Never commit .env, cookies, credentials, or private configuration. The generated
briefs are public. Existing unmodified originals from setup are backed up locally
under the ignored .uv/horizon-source-backups/ folder.

Brief dates use UTC, so an evening Pacific run may be dated the following day.
