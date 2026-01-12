"""Tests for PartyWorkspaceManager."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from takopi_party.workspace import (
    PartyWorkspaceManager,
    WorkspaceError,
    _sanitize_project_name,
)


@pytest.fixture
def workspace_base(tmp_path: Path) -> Path:
    """Return a temporary path for workspace base."""
    base = tmp_path / "party"
    base.mkdir()
    return base


@pytest.fixture
def manager(workspace_base: Path) -> PartyWorkspaceManager:
    """Create a workspace manager for each test."""
    return PartyWorkspaceManager(workspace_base)


class TestSanitizeProjectName:
    """Tests for project name sanitization."""

    def test_lowercase(self) -> None:
        """Test that names are lowercased."""
        assert _sanitize_project_name("MyProject") == "myproject"
        assert _sanitize_project_name("ALLCAPS") == "allcaps"

    def test_spaces_to_hyphens(self) -> None:
        """Test that spaces become hyphens."""
        assert _sanitize_project_name("My Project") == "my-project"
        assert _sanitize_project_name("My  Project") == "my-project"

    def test_special_chars_to_hyphens(self) -> None:
        """Test that special characters become hyphens."""
        assert _sanitize_project_name("My_Project!") == "my-project"
        assert _sanitize_project_name("Project@2024") == "project-2024"

    def test_strips_leading_trailing_hyphens(self) -> None:
        """Test that leading/trailing hyphens are removed."""
        assert _sanitize_project_name("@project@") == "project"
        assert _sanitize_project_name("---test---") == "test"

    def test_collapses_multiple_hyphens(self) -> None:
        """Test that multiple hyphens collapse to one."""
        assert _sanitize_project_name("my---project") == "my-project"
        assert _sanitize_project_name("a - b - c") == "a-b-c"

    def test_empty_returns_project(self) -> None:
        """Test that empty/invalid names return 'project'."""
        assert _sanitize_project_name("") == "project"
        assert _sanitize_project_name("@@@") == "project"


class TestPartyWorkspaceManager:
    """Tests for PartyWorkspaceManager."""

    def test_base_path(self, manager: PartyWorkspaceManager, workspace_base: Path) -> None:
        """Test that base_path is correct."""
        assert manager.base_path == workspace_base

    def test_personal_workspace_path(self, manager: PartyWorkspaceManager) -> None:
        """Test personal workspace path generation."""
        path = manager.personal_workspace_path(12345)
        assert path.name == "12345"
        assert path.parent == manager.base_path

    def test_project_workspace_path(self, manager: PartyWorkspaceManager) -> None:
        """Test project workspace path generation."""
        path = manager.project_workspace_path("My Project")
        assert path.name == "my-project"
        assert path.parent == manager.base_path

    def test_workspace_exists(self, manager: PartyWorkspaceManager) -> None:
        """Test workspace existence check."""
        path = manager.personal_workspace_path(12345)
        assert manager.workspace_exists(path) is False

        path.mkdir(parents=True)
        assert manager.workspace_exists(path) is True

    def test_create_workspace_success(self, manager: PartyWorkspaceManager) -> None:
        """Test successful workspace creation."""
        path = manager.personal_workspace_path(12345)
        result = manager.create_workspace(path, "Alice", 12345)

        assert result == path
        assert path.exists()
        assert (path / "README.md").exists()
        assert (path / ".git").is_dir()

        # Verify git config
        proc = subprocess.run(
            ["git", "config", "user.name"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        assert proc.stdout.strip() == "Alice"

        proc = subprocess.run(
            ["git", "config", "user.email"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        assert proc.stdout.strip() == "party-12345@takopi.local"

    def test_create_workspace_already_exists(self, manager: PartyWorkspaceManager) -> None:
        """Test that creating workspace twice raises error."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        with pytest.raises(WorkspaceError, match="already exists"):
            manager.create_workspace(path, "Alice", 12345)

    def test_create_workspace_has_initial_commit(self, manager: PartyWorkspaceManager) -> None:
        """Test that workspace has an initial commit."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        proc = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=path,
            capture_output=True,
            text=True,
        )
        assert "Initial commit" in proc.stdout

    def test_archive_workspace(self, manager: PartyWorkspaceManager) -> None:
        """Test archiving a workspace."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        archive_path = manager.archive_workspace(path)

        assert archive_path is not None
        assert archive_path.exists()
        assert not path.exists()
        assert archive_path.parent.name == "archived"
        assert "12345_" in archive_path.name

    def test_archive_nonexistent_workspace(self, manager: PartyWorkspaceManager) -> None:
        """Test archiving a workspace that doesn't exist."""
        path = manager.personal_workspace_path(99999)
        result = manager.archive_workspace(path)
        assert result is None

    def test_delete_workspace(self, manager: PartyWorkspaceManager) -> None:
        """Test deleting a workspace."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        result = manager.delete_workspace(path)

        assert result is True
        assert not path.exists()

    def test_delete_nonexistent_workspace(self, manager: PartyWorkspaceManager) -> None:
        """Test deleting a workspace that doesn't exist."""
        path = manager.personal_workspace_path(99999)
        result = manager.delete_workspace(path)
        assert result is False

    def test_cleanup_workspace_archive(self, manager: PartyWorkspaceManager) -> None:
        """Test cleanup with archive=True."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        archive_path = manager.cleanup_workspace(path, archive=True)

        assert archive_path is not None
        assert archive_path.exists()
        assert not path.exists()

    def test_cleanup_workspace_delete(self, manager: PartyWorkspaceManager) -> None:
        """Test cleanup with archive=False."""
        path = manager.personal_workspace_path(12345)
        manager.create_workspace(path, "Alice", 12345)

        result = manager.cleanup_workspace(path, archive=False)

        assert result is None
        assert not path.exists()

    def test_project_workspace_sanitization(self, manager: PartyWorkspaceManager) -> None:
        """Test that project workspaces use sanitized names."""
        path = manager.project_workspace_path("My Awesome Project!")
        manager.create_workspace(path, "My Awesome Project!", 12345)

        assert path.name == "my-awesome-project"
        assert path.exists()

    def test_multiple_workspaces(self, manager: PartyWorkspaceManager) -> None:
        """Test creating multiple workspaces."""
        path1 = manager.personal_workspace_path(12345)
        path2 = manager.project_workspace_path("ProjectA")
        path3 = manager.project_workspace_path("ProjectB")

        manager.create_workspace(path1, "Alice", 12345)
        manager.create_workspace(path2, "ProjectA", 12345)
        manager.create_workspace(path3, "ProjectB", 12345)

        assert path1.exists()
        assert path2.exists()
        assert path3.exists()
