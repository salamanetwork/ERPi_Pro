"""Read manifests without importing extension packages."""
import json
import re

from django.core.exceptions import ImproperlyConfigured
from packaging.specifiers import InvalidSpecifier, SpecifierSet

PLATFORM_VERSION = "0.1"
RELEASE_VERSION = "2.9.0"


def extension_manifests(root, packages):
    for package in packages:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", package):
            raise ImproperlyConfigured(f"Invalid ERP extension package name: {package!r}")
        candidate = root / package / "manifest.json"
        if not candidate.is_file() and (root / "modules" / package / "manifest.json").is_file():
            candidate = root / "modules" / package / "manifest.json"
        manifest = json.loads(candidate.read_text())
        if manifest.get("id") != package:
            raise ImproperlyConfigured(f"ERP extension {package} must declare the same manifest id")
        yield package, manifest


def compatibility_issue(manifest, release=RELEASE_VERSION):
    if manifest.get("platform_compatibility") != PLATFORM_VERSION:
        return "platform_contract"
    requirement = manifest.get("core_version_range")
    if requirement is None:
        return ""
    if not isinstance(requirement, str) or not requirement.strip():
        return "invalid_core_version_range"
    try:
        return "" if SpecifierSet(requirement).contains(release, prereleases=False) else "core_version"
    except InvalidSpecifier:
        return "invalid_core_version_range"


def compatible_modules(manifests):
    compatible = {key for key,value in manifests.items() if not compatibility_issue(value)}
    while True:
        available = {key for key in compatible if not (
            set(manifests[key].get("required_dependencies", [])) & (manifests.keys() - compatible))}
        if available == compatible:
            return available
        compatible = available


def effective_modules(manifests, requested):
    core = {key for key, m in manifests.items() if m.get("category") == "platform_core"}
    return (set(requested) | core) & compatible_modules(manifests)
