"""
Set the SIS release version everywhere it appears, in both repositories.

    python scripts/set_version.py 1.0.1
    python scripts/set_version.py 1.1.0-rc.1 --frontend "D:/SIS Frontend"

Updates the backend's VERSION file, the frontend's package.json and package-lock.json, and the version
shown in the user guide (public/docs/index.html). It does not commit or tag: add the CHANGELOG.md entry,
commit, then tag both repositories (see docs/developers/releasing.md).
"""
import argparse
import json
import re
import sys
from pathlib import Path

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$")
BACKEND = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description="Set the SIS release version in both repositories.")
    parser.add_argument("version", help="semantic version, e.g. 1.0.1 or 1.1.0-rc.1")
    parser.add_argument("--frontend", default=str(BACKEND.parent / "SIS Frontend"),
                        help="path to the frontend repository (default: next to this one)")
    args = parser.parse_args()
    version = args.version.strip().lstrip("v")
    if not SEMVER.match(version):
        sys.exit(f"'{args.version}' is not a semantic version such as 1.0.1 or 1.1.0-rc.1")
    frontend = Path(args.frontend)
    package_json = frontend / "package.json"
    if not package_json.exists():
        sys.exit(f"No package.json in {frontend}: pass --frontend with the frontend repository's path")

    (BACKEND / "VERSION").write_text(version + "\n", encoding="utf-8")

    for name in ("package.json", "package-lock.json"):
        path = frontend / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = version
        if name == "package-lock.json" and "" in data.get("packages", {}):
            data["packages"][""]["version"] = version
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    guide = frontend / "public" / "docs" / "index.html"
    if guide.exists():
        text = guide.read_text(encoding="utf-8")
        text, count = re.subn(r"(<span data-guide-version>)[^<]*(</span>)", rf"\g<1>{version}\g<2>", text)
        guide.write_text(text, encoding="utf-8")
        print(f"User guide: {count} version mention(s) updated")

    print(f"Version set to {version} in both repositories.")
    print("Next: add the CHANGELOG.md entry, commit both, then tag both: "
          f"git tag -a v{version} -m \"SIS {version}\"")


if __name__ == "__main__":
    main()
