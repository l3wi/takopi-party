"""Tests for the config module."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli

from takopi_party.config import (
    _make_project_key,
    add_party_project,
    get_party_project_key,
    remove_party_project,
)


class TestMakeProjectKey:
    """Tests for _make_project_key function."""

    def test_personal_topic_simple_name(self):
        """Personal topics get party-user- prefix."""
        result = _make_project_key("John", is_personal=True)
        assert result == "party-user-john"

    def test_project_topic_simple_name(self):
        """Project topics get party- prefix."""
        result = _make_project_key("MyProject", is_personal=False)
        assert result == "party-myproject"

    def test_name_with_spaces(self):
        """Spaces are converted to hyphens."""
        result = _make_project_key("John Doe", is_personal=True)
        assert result == "party-user-john-doe"

    def test_name_with_underscores(self):
        """Underscores are converted to hyphens."""
        result = _make_project_key("my_project", is_personal=False)
        assert result == "party-my-project"

    def test_name_with_special_chars(self):
        """Special characters are removed."""
        result = _make_project_key("Project@#$123!", is_personal=False)
        assert result == "party-project123"

    def test_consecutive_hyphens_collapsed(self):
        """Multiple consecutive hyphens are collapsed to one."""
        result = _make_project_key("a   b", is_personal=False)
        assert result == "party-a-b"

    def test_leading_trailing_hyphens_stripped(self):
        """Leading and trailing hyphens are stripped."""
        result = _make_project_key("-test-", is_personal=False)
        assert result == "party-test"

    def test_unicode_name(self):
        """Unicode characters are handled."""
        result = _make_project_key("Test123", is_personal=False)
        assert result == "party-test123"


class TestGetPartyProjectKey:
    """Tests for get_party_project_key function."""

    def test_returns_same_as_make(self):
        """get_party_project_key returns the same as _make_project_key."""
        assert get_party_project_key("Test", True) == _make_project_key("Test", True)
        assert get_party_project_key("Test", False) == _make_project_key("Test", False)


class TestAddPartyProject:
    """Tests for add_party_project function."""

    def test_adds_project_to_empty_config(self, tmp_path: Path):
        """Adds project to config with no projects section."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text('default_engine = "claude"\n')

        workspace_path = tmp_path / "workspace"

        project_key = add_party_project(
            config_path, workspace_path, "TestProject", is_personal=False
        )

        assert project_key == "party-testproject"

        # Verify config was updated
        with config_path.open("rb") as f:
            config = tomli.load(f)

        assert "projects" in config
        assert "party-testproject" in config["projects"]
        assert config["projects"]["party-testproject"]["path"] == str(workspace_path)
        assert config["projects"]["party-testproject"]["default_engine"] == "claude"

    def test_adds_project_to_existing_projects(self, tmp_path: Path):
        """Adds project to config with existing projects."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text(
            """
default_engine = "claude"

[projects.existing]
path = "/some/path"
"""
        )

        workspace_path = tmp_path / "workspace"

        project_key = add_party_project(
            config_path, workspace_path, "NewProject", is_personal=False
        )

        assert project_key == "party-newproject"

        with config_path.open("rb") as f:
            config = tomli.load(f)

        # Both projects should exist
        assert "existing" in config["projects"]
        assert "party-newproject" in config["projects"]

    def test_adds_personal_project(self, tmp_path: Path):
        """Adds personal project with correct prefix."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text('default_engine = "claude"\n')

        workspace_path = tmp_path / "workspace"

        project_key = add_party_project(config_path, workspace_path, "John Doe", is_personal=True)

        assert project_key == "party-user-john-doe"

        with config_path.open("rb") as f:
            config = tomli.load(f)

        assert "party-user-john-doe" in config["projects"]

    def test_raises_on_duplicate_key(self, tmp_path: Path):
        """Raises ValueError if project key already exists."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text(
            """
[projects.party-test]
path = "/existing"
"""
        )

        workspace_path = tmp_path / "workspace"

        with pytest.raises(ValueError, match="already exists"):
            add_party_project(config_path, workspace_path, "Test", is_personal=False)

    def test_raises_on_missing_config(self, tmp_path: Path):
        """Raises FileNotFoundError if config doesn't exist."""
        config_path = tmp_path / "nonexistent.toml"
        workspace_path = tmp_path / "workspace"

        with pytest.raises(FileNotFoundError):
            add_party_project(config_path, workspace_path, "Test", is_personal=False)


class TestRemovePartyProject:
    """Tests for remove_party_project function."""

    def test_removes_existing_project(self, tmp_path: Path):
        """Removes an existing project from config."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text(
            """
[projects.party-test]
path = "/some/path"

[projects.other]
path = "/other/path"
"""
        )

        result = remove_party_project(config_path, "Test", is_personal=False)

        assert result is True

        with config_path.open("rb") as f:
            config = tomli.load(f)

        assert "party-test" not in config["projects"]
        assert "other" in config["projects"]

    def test_removes_personal_project(self, tmp_path: Path):
        """Removes personal project with correct key."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text(
            """
[projects.party-user-john]
path = "/some/path"
"""
        )

        result = remove_party_project(config_path, "John", is_personal=True)

        assert result is True

        with config_path.open("rb") as f:
            config = tomli.load(f)

        assert "party-user-john" not in config["projects"]

    def test_returns_false_if_not_found(self, tmp_path: Path):
        """Returns False if project key doesn't exist."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text(
            """
[projects.other]
path = "/some/path"
"""
        )

        result = remove_party_project(config_path, "Test", is_personal=False)

        assert result is False

    def test_returns_false_if_no_projects_section(self, tmp_path: Path):
        """Returns False if config has no projects section."""
        config_path = tmp_path / "takopi.toml"
        config_path.write_text('default_engine = "claude"\n')

        result = remove_party_project(config_path, "Test", is_personal=False)

        assert result is False

    def test_returns_false_if_config_missing(self, tmp_path: Path):
        """Returns False if config file doesn't exist."""
        config_path = tmp_path / "nonexistent.toml"

        result = remove_party_project(config_path, "Test", is_personal=False)

        assert result is False
