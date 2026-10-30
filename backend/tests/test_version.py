import json
from pathlib import Path

from app.version import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_backend_version_matches_package_json():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == APP_VERSION


def test_version_is_documented():
    """Новая версия описана для пользователей, администраторов, в CHANGELOG и в release notes."""
    docs = ROOT / "docs"
    assert f"## Версия {APP_VERSION}" in (docs / "versions.md").read_text(encoding="utf-8")
    assert f"## Версия {APP_VERSION}" in (docs / "versions_admin.md").read_text(encoding="utf-8")
    assert f"## v{APP_VERSION}" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (docs / "release-notes" / f"v{APP_VERSION}.md").exists()
