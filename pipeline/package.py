"""Packs the skill, optionally with a built profile, into a zip for claude.ai.

Usage:
    ./package.sh [--profile profiles/me] [-o dist/humanize.zip]

The archive holds one ``humanize/`` folder, as claude.ai expects. Its description is
swapped for the short one, because claude.ai caps descriptions at 200 characters. The
corpus selection (``corpus.toml``) and the blind-test holdout never go in.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

from skill.scripts import profilelib

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skill"
ROOT = "humanize"
MAX_DESCRIPTION = 200
MAX_NAME = 64

SKILL_FILES = ("SKILL.md", "references/*.md", "references/*.txt", "scripts/*.py")
PROFILE_FILES = (
    "profile.md",
    "settings.toml",
    "stats.json",
    "registers/*/guide.md",
    "registers/*/facts.md",
    "registers/*/examples.md",
    "registers/*/pairs.jsonl",
)

_DESCRIPTION = re.compile(r"^description:.*?(?=^\w+:|^---)", re.M | re.S)


class PackageError(ValueError):
    """The skill or profile cannot be packaged as is."""


def short_skill_md(text: str, description: str) -> str:
    """Replaces the frontmatter description with ``description``."""
    if len(description) > MAX_DESCRIPTION:
        raise PackageError(f"short description is {len(description)} chars, max {MAX_DESCRIPTION}")
    escaped = description.replace("\\", "\\\\").replace('"', '\\"')
    new, n = _DESCRIPTION.subn(f'description: "{escaped}"\n', text, count=1)
    if n != 1:
        raise PackageError("SKILL.md has no description in its frontmatter")
    return new


def untouched_guides(profile: Path) -> list[str]:
    """Registers whose guide.md still has no text under any heading."""
    names = []
    for guide in sorted(profile.glob("registers/*/guide.md")):
        body = re.sub(r"<!--.*?-->", "", guide.read_text(encoding="utf-8"), flags=re.S)
        content = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
        if not content:
            names.append(guide.parent.name)
    return names


def collect(profile: Path | None) -> dict[str, Path]:
    """Maps archive paths to source files."""
    files: dict[str, Path] = {}
    for pattern in SKILL_FILES:
        for src in sorted(SKILL.glob(pattern)):
            files[f"{ROOT}/{src.relative_to(SKILL).as_posix()}"] = src
    if profile is not None:
        if not (profile / "profile.md").exists() or not (profile / "stats.json").exists():
            raise PackageError(f"{profile}: no profile.md or stats.json, run build_profile first")
        for pattern in PROFILE_FILES:
            for src in sorted(profile.glob(pattern)):
                files[f"{ROOT}/profile/{src.relative_to(profile).as_posix()}"] = src
    return files


def build_zip(profile: Path | None, out: Path) -> tuple[int, int]:
    """Writes the archive.

    Returns:
        ``(file_count, bytes)`` of the written zip.

    Raises:
        PackageError: Missing profile files or a description over the limit.
    """
    files = collect(profile)
    short = (SKILL / "description-short.txt").read_text(encoding="utf-8").strip()
    skill_md = short_skill_md((SKILL / "SKILL.md").read_text(encoding="utf-8"), short)
    out.parent.mkdir(parents=True, exist_ok=True)
    count = len(files)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, src in files.items():
            if arcname == f"{ROOT}/SKILL.md":
                zf.writestr(arcname, skill_md)
            else:
                zf.write(src, arcname)
        if profile is not None and (profile / "settings.toml").exists():
            # The sandbox may lack tomllib (Python < 3.11); profilelib falls back to this.
            settings = profilelib.read_toml(profile / "settings.toml")
            zf.writestr(f"{ROOT}/profile/settings.json", json.dumps(settings, ensure_ascii=False))
            count += 1
    return count, out.stat().st_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack the skill for claude.ai.")
    parser.add_argument("--profile", type=Path, help="built profile directory to include")
    parser.add_argument(
        "-o", "--out", type=Path, help="zip path (default dist/humanize[-name].zip)"
    )
    args = parser.parse_args(argv)
    suffix = f"-{args.profile.name}" if args.profile else ""
    out = args.out or REPO / "dist" / f"humanize{suffix}.zip"
    try:
        count, size = build_zip(args.profile, out)
    except PackageError as exc:
        print(f"package: {exc}", file=sys.stderr)
        return 2
    print(f"{count} files, {size / 1024:.0f} KB -> {out}")
    if args.profile:
        empty = untouched_guides(args.profile)
        if empty:
            print(f"warning: guide.md is still empty for: {', '.join(empty)}", file=sys.stderr)
    print("Upload it in claude.ai: Settings > Capabilities > Skills (code execution must be on).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
