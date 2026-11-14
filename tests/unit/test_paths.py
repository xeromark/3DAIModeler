from pathlib import Path

import pytest

from threedai_modeler.core.paths import (
    ENV_WORKSPACE,
    Workspace,
    WorkspaceNotFoundError,
    find_workspace_root,
)


def make_workspace(root: Path) -> Path:
    (root / "src" / "threedai_modeler").mkdir(parents=True)
    (root / "compose.yaml").write_text("services: {}\n")
    return root


def test_finds_root_walking_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_WORKSPACE, raising=False)
    root = make_workspace(tmp_path / "ws")
    deep = root / "src" / "threedai_modeler" / "core"
    deep.mkdir()
    assert find_workspace_root(deep / "paths.py") == root.resolve()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_workspace(tmp_path / "ws")
    monkeypatch.setenv(ENV_WORKSPACE, str(root))
    assert find_workspace_root(Path("/")) == root.resolve()


def test_env_override_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_WORKSPACE, str(tmp_path))
    with pytest.raises(WorkspaceNotFoundError):
        find_workspace_root()


def test_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_WORKSPACE, raising=False)
    with pytest.raises(WorkspaceNotFoundError):
        find_workspace_root(tmp_path)


def test_runtime_paths_stay_inside_workspace(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    runtime = [ws.comfy_models_dir, ws.jobs_dir, ws.logs_dir, ws.cache_dir, ws.user_registry_file]
    for path in runtime:
        assert path.is_relative_to(ws.engine_dir)


def test_discovers_real_repo() -> None:
    ws = Workspace.discover()
    assert ws.compose_file.is_file()
