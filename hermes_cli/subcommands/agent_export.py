"""``hermes export-agent`` subcommand parser."""

from __future__ import annotations


def build_export_agent_parser(subparsers) -> None:
    """Attach the ``export-agent`` subcommand to ``subparsers``."""
    from hermes_cli.openclaw_export import cmd_export_agent

    export_parser = subparsers.add_parser(
        "export-agent",
        help="Export this Hermes setup into another agent's format",
        description="Export the active Hermes home into another agent's format. "
        "Currently only OpenClaw is supported: model, providers, skills, "
        "workspace files and memory become openclaw.json + agents/<id>/, "
        "while plugins, sessions and cron jobs are archived, never activated. "
        "Secrets are never copied — keys and tokens leave as ${VAR} references.",
    )
    export_subparsers = export_parser.add_subparsers(dest="export_agent")

    openclaw = export_subparsers.add_parser(
        "openclaw",
        help="Export this Hermes setup into OpenClaw format",
        description="Write a fresh OpenClaw workspace (openclaw.json, "
        "agents/<id>/models.json + workspace files, .env.example) from the "
        "active Hermes home, with plugins, sessions and cron jobs archived "
        "for manual review. Always previews the plan first and asks before "
        "writing anything.",
    )
    openclaw.add_argument(
        "--source", help="Hermes home to export (default: the active profile home)"
    )
    openclaw.add_argument(
        "--out",
        help="Output directory (default: ./openclaw-export-<agent>-<timestamp>)",
    )
    openclaw.add_argument(
        "--agent-id", help="OpenClaw agent id (default: main for the default profile)"
    )
    openclaw.add_argument(
        "--dry-run", action="store_true", help="Preview only, write nothing"
    )
    openclaw.add_argument(
        "--overwrite", action="store_true", help="Replace existing files on conflicts"
    )
    openclaw.add_argument(
        "-y", "--yes", action="store_true", help="Skip the confirmation prompt"
    )
    openclaw.add_argument(
        "--zip",
        action="store_true",
        help="Also write the export as a .zip beside the directory",
    )
    openclaw.set_defaults(func=cmd_export_agent)
    export_parser.set_defaults(func=cmd_export_agent)
