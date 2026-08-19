#!/usr/bin/env python3
"""Rename `platform/` package dir → `tgju/` (stdlib `platform` shadowing fix).

Only rewrites true package/path references — product-name uses of the word
"platform" (tgju-telegram-platform, "WhatsApp platform", APP.md prose) are
left untouched. Reversible via git.
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD, NEW = "platform", "tgju"

# relpath → list of (old, new) exact-string replacements, applied in order
REPLACEMENTS = {
    # CI
    ".github/workflows/ci.yml": [
        ("python -m compileall -q platform tests", "python -m compileall -q tgju tests"),
        ("import platform.tgju_engine_format; import platform.tgju_core",
         "import tgju.tgju_engine_format; import tgju.tgju_core"),
    ],
    # Dockerfile
    "Dockerfile": [
        ("COPY platform/ platform/", "COPY tgju/ tgju/"),
        ("RUN mkdir -p /app/platform/state", "RUN mkdir -p /app/tgju/state"),
        ('CMD ["python", "platform/tgju_platform.py"]', 'CMD ["python", "tgju/tgju_platform.py"]'),
    ],
    # pyproject
    "pyproject.toml": [
        ('packages = ["platform", "platform.tgju_core"]',
         'packages = ["tgju", "tgju.tgju_core"]'),
        ("tgju-platform = \"tgju_platform:main\"", "tgju-platform = \"tgju.tgju_platform:main\""),
    ],
    # Launchers
    "start-platform.bat": [('cd /d "%~dp0platform"', 'cd /d "%~dp0tgju"')],
    "start-platform.sh": [("cd platform", "cd tgju")],
    # Tests
    "tests/test_config.py": [("import platform.tgju_engine_config as cfg",
                              "import tgju.tgju_engine_config as cfg")],
    "tests/test_core_idempotency.py": [
        ("from platform.tgju_core.idempotency import",
         "from tgju.tgju_core.idempotency import")],
    "tests/test_format.py": [("import platform.tgju_engine_format as fmt",
                              "import tgju.tgju_engine_format as fmt")],
    # Docs that reference the filesystem path (product prose untouched)
    "CHANGELOG.md": [("`platform/channels.yaml`", "`tgju/channels.yaml`"),
                     ("`platform/tgju_core/`", "`tgju/tgju_core/`")],
    "CONTRIBUTING.md": [("`platform/tgju_core/`", "`tgju/tgju_core/`"),
                        ("compileall -q platform tests", "compileall -q tgju tests")],
    "README.md": [("`platform/state/bot_profile.json`", "`tgju/state/bot_profile.json`")],
    "APP.md": [
        ("`platform/state/`", "`tgju/state/`"),
        ("`platform/channels.yaml`", "`tgju/channels.yaml`"),
        ("| `platform/", "| `tgju/"),
        ("( `platform/", "( `tgju/"),
        ("`platform/tgju_platform.py`", "`tgju/tgju_platform.py`"),
        ("`platform/tgju_platform_ui.html`", "`tgju/tgju_platform_ui.html`"),
        ("`platform/tgju_core/`", "`tgju/tgju_core/`"),
    ],
    "platform/apply_css.py": [
        (r"D:\Hermes\TGJU-Telegram\platform\new_css_block.txt",
         r"D:\Hermes\TGJU-Telegram\tgju\new_css_block.txt"),
    ],
    "platform/tgju_multi.py": [
        ("python -m platform.tgju_multi", "python -m tgju.tgju_multi"),
    ],
    "platform/tgju_platform.py": [],  # content refs handled below
    "platform/tgju_engine_bot.py": [],
    "platform/tgju_engine_config.py": [],
    "platform/tgju_core_integration.py": [],
    "platform/tgju_engine_whatsapp.py": [],
    "platform/tgju_engine_bale.py": [],
    "SECURITY.md": [],
}

# Inside the platform dir itself: any "platform." import → "tgju."
IMPORT_RE = re.compile(r"\bplatform\.(tgju_\w+|tgju_core\b)")

def main():
    if not os.path.isdir(os.path.join(ROOT, OLD)):
        print(f"error: {OLD}/ not found")
        return 1
    if os.path.exists(os.path.join(ROOT, NEW)):
        print(f"error: {NEW}/ already exists")
        return 1

    changed_files = []

    # 1. Apply explicit replacements (all files listed above)
    for rel, pairs in REPLACEMENTS.items():
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            s = f.read()
        orig = s
        for old, new in pairs:
            s = s.replace(old, new)
        if s != orig:
            with open(path, "w", encoding="utf-8") as f:
                f.write(s)
            changed_files.append(rel)

    # 2. Fix imports inside the platform/ tree (every .py)
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, OLD)):
        dirnames[:] = [d for d in dirnames if d not in {".venv", "__pycache__", "state"}]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            with open(p, encoding="utf-8") as f:
                s = f.read()
            orig = s
            s = IMPORT_RE.sub(lambda m: "tgju." + m.group(1), s)
            if s != orig:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(s)
                changed_files.append(os.path.relpath(p, ROOT))

    # 3. Rename the directory
    shutil.move(os.path.join(ROOT, OLD), os.path.join(ROOT, NEW))

    print(f"✓ renamed {OLD}/ → {NEW}/")
    print(f"✓ rewrote {len(changed_files)} files:")
    for c in changed_files:
        print(f"    - {c}")
    print("\nNext: git add -A && commit; pytest; push.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
