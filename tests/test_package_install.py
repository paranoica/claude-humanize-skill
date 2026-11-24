import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from pipeline import package
from tests.test_build_profile import _corpus

REPO = Path(__file__).resolve().parent.parent


class TestShortSkillMd:
    SRC = "---\nname: humanize\ndescription: >-\n  long\n  text\n---\n\n# Body\n"

    def test_replaced(self) -> None:
        out = package.short_skill_md(self.SRC, 'short "one"')
        assert out == '---\nname: humanize\ndescription: "short \\"one\\""\n---\n\n# Body\n'

    def test_too_long(self) -> None:
        with pytest.raises(package.PackageError, match="200"):
            package.short_skill_md(self.SRC, "x" * 201)

    def test_limit_exact(self) -> None:
        assert "x" * 200 in package.short_skill_md(self.SRC, "x" * 200)

    def test_real_skill_md(self) -> None:
        text = (REPO / "skill" / "SKILL.md").read_text(encoding="utf-8")
        out = package.short_skill_md(text, "short")
        assert out.startswith('---\nname: humanize\ndescription: "short"\n---\n')
        assert out.endswith(text.split("---", 2)[2])


def _names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as zf:
        return set(zf.namelist())


class TestZip:
    def test_without_profile(self, tmp_path: Path) -> None:
        out = tmp_path / "h.zip"
        package.build_zip(None, out)
        names = _names(out)
        assert "humanize/SKILL.md" in names
        assert "humanize/scripts/lint.py" in names
        assert "humanize/references/stoplist.txt" in names
        assert not any("profile/" in n or "description-short" in n or "pycache" in n for n in names)
        assert all(n.startswith("humanize/") for n in names)

    def test_with_profile(self, tmp_path: Path) -> None:
        from pipeline import build_profile

        prof = tmp_path / "me"
        build_profile.build(prof, _corpus(), holdout_size=3)
        (prof / "corpus.toml").write_text('[[register]]\nname = "ru"\nlang = "ru"\n')
        out = tmp_path / "h.zip"
        package.build_zip(prof, out)
        names = _names(out)
        for name in ("profile.md", "settings.toml", "stats.json", "registers/ru/examples.md"):
            assert f"humanize/profile/{name}" in names
        assert "humanize/profile/holdout.jsonl" not in names
        assert "humanize/profile/corpus.toml" not in names
        with zipfile.ZipFile(out) as zf:
            settings = json.loads(zf.read("humanize/profile/settings.json"))
        assert settings["rules"]["profanity"] == "mirror"

    def test_unbuilt_profile(self, tmp_path: Path) -> None:
        with pytest.raises(package.PackageError, match="build_profile"):
            package.build_zip(tmp_path, tmp_path / "h.zip")

    def test_untouched_guides(self, tmp_path: Path) -> None:
        from pipeline import build_profile

        for name, text in (("a", None), ("b", "## Как звучит\nкоротко и строчными\n")):
            d = tmp_path / "registers" / name
            d.mkdir(parents=True)
            (d / "guide.md").write_text(text or build_profile.render_guide_template(name, "ru"))
        assert package.untouched_guides(tmp_path) == ["a"]


def _install(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
    return subprocess.run(
        ["bash", str(REPO / "install.sh"), *args], env=env, capture_output=True, text=True
    )


def _hooks(home: Path) -> list[dict]:
    data = json.loads((home / "settings.json").read_text())
    return data.get("hooks", {}).get("UserPromptSubmit", [])


class TestInstall:
    HOOK = str(REPO / "hooks" / "draft_trigger.sh")

    def test_fresh_install_and_idempotent(self, tmp_path: Path) -> None:
        assert _install(tmp_path).returncode == 0
        assert _install(tmp_path).returncode == 0
        link = tmp_path / "skills" / "humanize"
        assert link.is_symlink() and link.resolve() == REPO / "skill"
        commands = [h["command"] for g in _hooks(tmp_path) for h in g["hooks"]]
        assert commands == [self.HOOK]

    def test_keeps_other_settings_and_moves_old_skill(self, tmp_path: Path) -> None:
        other = {"type": "command", "command": "/bin/other"}
        settings = {"model": "x", "hooks": {"UserPromptSubmit": [{"hooks": [other]}]}}
        (tmp_path / "settings.json").write_text(json.dumps(settings))
        old = tmp_path / "skills" / "humanize"
        old.mkdir(parents=True)
        (old / "SKILL.md").write_text("old")
        assert _install(tmp_path).returncode == 0
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["model"] == "x"
        commands = [h["command"] for g in _hooks(tmp_path) for h in g["hooks"]]
        assert commands == ["/bin/other", self.HOOK]
        backups = list((tmp_path / "backups").iterdir())
        assert len(backups) == 1 and (backups[0] / "SKILL.md").read_text() == "old"
        assert not any(
            p.name.startswith("humanize") for p in (tmp_path / "skills").iterdir() if p != old
        )
        assert list(tmp_path.glob("settings.json.bak-*"))

    def test_uninstall(self, tmp_path: Path) -> None:
        other = {"type": "command", "command": "/bin/other"}
        (tmp_path / "settings.json").write_text(
            json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [other]}]}})
        )
        _install(tmp_path)
        assert _install(tmp_path, "--uninstall").returncode == 0
        assert not (tmp_path / "skills" / "humanize").exists()
        assert json.loads((tmp_path / "settings.json").read_text()) == {
            "hooks": {"UserPromptSubmit": [{"hooks": [other]}]}
        }

    def test_uninstall_leaves_no_empty_hooks(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text(json.dumps({"model": "x"}))
        _install(tmp_path)
        _install(tmp_path, "--uninstall")
        assert json.loads((tmp_path / "settings.json").read_text()) == {"model": "x"}

    def test_no_hook(self, tmp_path: Path) -> None:
        assert _install(tmp_path, "--no-hook").returncode == 0
        assert not (tmp_path / "settings.json").exists()

    @pytest.mark.parametrize("name", ["../etc", "a/b", "missing"])
    def test_bad_profile(self, tmp_path: Path, name: str) -> None:
        assert _install(tmp_path, "--profile", name).returncode != 0
        assert not (tmp_path / "skills").exists()


def test_hook_command_is_shell_quoted(tmp_path: Path) -> None:
    import shlex

    repo = tmp_path / "my repo"
    shutil.copytree(REPO / "hooks", repo / "hooks")
    shutil.copy(REPO / "install.sh", repo / "install.sh")
    (repo / "skill").mkdir()
    home = tmp_path / "home"
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(home)}
    subprocess.run(["bash", str(repo / "install.sh")], env=env, check=True, capture_output=True)
    command = _hooks(home)[0]["hooks"][0]["command"]
    assert command == shlex.quote(str(repo / "hooks" / "draft_trigger.sh"))
    ran = subprocess.run(
        command, shell=True, input='{"prompt": "ответь ему"}', capture_output=True, text=True
    )
    assert "humanize" in ran.stdout
    subprocess.run(
        ["bash", str(repo / "install.sh"), "--uninstall"], env=env, check=True, capture_output=True
    )
    assert _hooks(home) == []
