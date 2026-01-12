"""Party workspace management - creates and manages isolated workspaces."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


class WorkspaceError(Exception):
    """Error during workspace operations."""


def _sanitize_project_name(name: str) -> str:
    """Sanitize project name for use as folder name.

    Converts to lowercase, replaces spaces and special chars with hyphens.
    """
    # Lowercase and replace spaces/special chars with hyphens
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip("-")
    # Collapse multiple hyphens
    sanitized = re.sub(r"-+", "-", sanitized)
    return sanitized or "project"


class PartyWorkspaceManager:
    """Manages isolated workspaces for party topics."""

    def __init__(self, base_path: Path) -> None:
        self._base = base_path

    @property
    def base_path(self) -> Path:
        """Get the base path for all workspaces."""
        return self._base

    def project_workspace_path(self, project_name: str) -> Path:
        """Get the workspace path for a named project."""
        sanitized = _sanitize_project_name(project_name)
        return self._base / sanitized

    def workspace_exists(self, path: Path) -> bool:
        """Check if a workspace exists at the given path."""
        return path.exists()

    def create_workspace(
        self,
        path: Path,
        display_name: str,
        owner_id: int,
    ) -> Path:
        """Create workspace folder and initialize git repo.

        Args:
            path: The workspace directory path
            display_name: Human-readable name for the workspace
            owner_id: The owner's Telegram ID (for git config)

        Returns:
            Path to the created workspace

        Raises:
            WorkspaceError: If workspace creation fails
        """
        if path.exists():
            raise WorkspaceError(f"Workspace already exists at {path}")

        try:
            path.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise WorkspaceError(f"Failed to create workspace directory: {exc}") from exc

        try:
            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=path,
                check=True,
                capture_output=True,
            )

            # Configure git user for this workspace
            subprocess.run(
                ["git", "config", "user.email", f"party-{owner_id}@takopi.local"],
                cwd=path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", display_name],
                cwd=path,
                check=True,
                capture_output=True,
            )

            # Create initial README
            readme = path / "README.md"
            readme.write_text(
                f"# Party Workspace: {display_name}\n\nCreated: {datetime.now(UTC).isoformat()}\n"
            )

            # Create initial commit
            subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            # Clean up on failure
            shutil.rmtree(path, ignore_errors=True)
            stderr = exc.stderr.decode() if exc.stderr else str(exc)
            raise WorkspaceError(f"Failed to initialize git repo: {stderr}") from exc
        except OSError as exc:
            shutil.rmtree(path, ignore_errors=True)
            raise WorkspaceError(f"Failed to create workspace files: {exc}") from exc

        return path

    def archive_workspace(self, path: Path) -> Path | None:
        """Archive a workspace by moving it to the archived folder.

        Args:
            path: The workspace path to archive

        Returns:
            Path to the archived workspace, or None if no workspace existed

        Raises:
            WorkspaceError: If archival fails
        """
        if not path.exists():
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        archive_dir = self._base / "archived"
        archive_path = archive_dir / f"{path.name}_{timestamp}"

        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            path.rename(archive_path)
        except OSError as exc:
            raise WorkspaceError(f"Failed to archive workspace: {exc}") from exc

        return archive_path

    def delete_workspace(self, path: Path) -> bool:
        """Permanently delete a workspace.

        Args:
            path: The workspace path to delete

        Returns:
            True if workspace was deleted, False if it didn't exist

        Raises:
            WorkspaceError: If deletion fails
        """
        if not path.exists():
            return False

        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise WorkspaceError(f"Failed to delete workspace: {exc}") from exc

        return True

    def cleanup_workspace(self, path: Path, *, archive: bool = True) -> Path | None:
        """Remove or archive workspace.

        Args:
            path: The workspace path
            archive: If True, archive the workspace; if False, delete it

        Returns:
            Path to archived workspace if archived, None otherwise
        """
        if archive:
            return self.archive_workspace(path)
        self.delete_workspace(path)
        return None
