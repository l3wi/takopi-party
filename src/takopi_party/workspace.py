"""Party workspace management - creates and manages isolated workspaces."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path


class WorkspaceError(Exception):
    """Error during workspace operations."""


class PartyWorkspaceManager:
    """Manages isolated workspaces for party members."""

    def __init__(self, base_path: Path) -> None:
        self._base = base_path

    @property
    def base_path(self) -> Path:
        """Get the base path for all workspaces."""
        return self._base

    def workspace_path(self, user_id: int) -> Path:
        """Get the workspace path for a user."""
        return self._base / str(user_id)

    def workspace_exists(self, user_id: int) -> bool:
        """Check if a workspace exists for a user."""
        return self.workspace_path(user_id).exists()

    def create_workspace(self, user_id: int, display_name: str) -> Path:
        """Create workspace folder and initialize git repo.

        Args:
            user_id: The user's Telegram ID
            display_name: Human-readable name for the workspace

        Returns:
            Path to the created workspace

        Raises:
            WorkspaceError: If workspace creation fails
        """
        workspace = self.workspace_path(user_id)

        if workspace.exists():
            raise WorkspaceError(f"Workspace already exists for user {user_id}")

        try:
            workspace.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise WorkspaceError(f"Failed to create workspace directory: {exc}") from exc

        try:
            # Initialize git repo
            subprocess.run(
                ["git", "init"],
                cwd=workspace,
                check=True,
                capture_output=True,
            )

            # Configure git user for this workspace
            subprocess.run(
                ["git", "config", "user.email", f"party-{user_id}@takopi.local"],
                cwd=workspace,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", display_name],
                cwd=workspace,
                check=True,
                capture_output=True,
            )

            # Create initial README
            readme = workspace / "README.md"
            readme.write_text(
                f"# Party Workspace: {display_name}\n\nCreated: {datetime.now(UTC).isoformat()}\n"
            )

            # Create initial commit
            subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=workspace,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            # Clean up on failure
            shutil.rmtree(workspace, ignore_errors=True)
            stderr = exc.stderr.decode() if exc.stderr else str(exc)
            raise WorkspaceError(f"Failed to initialize git repo: {stderr}") from exc
        except OSError as exc:
            shutil.rmtree(workspace, ignore_errors=True)
            raise WorkspaceError(f"Failed to create workspace files: {exc}") from exc

        return workspace

    def archive_workspace(self, user_id: int) -> Path | None:
        """Archive a workspace by moving it to the archived folder.

        Args:
            user_id: The user's Telegram ID

        Returns:
            Path to the archived workspace, or None if no workspace existed

        Raises:
            WorkspaceError: If archival fails
        """
        workspace = self.workspace_path(user_id)

        if not workspace.exists():
            return None

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        archive_dir = self._base / "archived"
        archive_path = archive_dir / f"{user_id}_{timestamp}"

        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            workspace.rename(archive_path)
        except OSError as exc:
            raise WorkspaceError(f"Failed to archive workspace: {exc}") from exc

        return archive_path

    def delete_workspace(self, user_id: int) -> bool:
        """Permanently delete a workspace.

        Args:
            user_id: The user's Telegram ID

        Returns:
            True if workspace was deleted, False if it didn't exist

        Raises:
            WorkspaceError: If deletion fails
        """
        workspace = self.workspace_path(user_id)

        if not workspace.exists():
            return False

        try:
            shutil.rmtree(workspace)
        except OSError as exc:
            raise WorkspaceError(f"Failed to delete workspace: {exc}") from exc

        return True

    def cleanup_workspace(self, user_id: int, *, archive: bool = True) -> Path | None:
        """Remove or archive workspace.

        Args:
            user_id: The user's Telegram ID
            archive: If True, archive the workspace; if False, delete it

        Returns:
            Path to archived workspace if archived, None otherwise
        """
        if archive:
            return self.archive_workspace(user_id)
        self.delete_workspace(user_id)
        return None
