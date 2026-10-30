import json
from pathlib import Path

from app.version import APP_VERSION

ROOT = Path(__file__).resolve().parents[2]


def test_backend_version_matches_package_json():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == APP_VERSION


def test_version_is_documented():
    versions = (ROOT / "docs" / "versions.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"v{APP_VERSION}" in versions
    assert f"v{APP_VERSION}" in changelog
