"""Export only the reviewed engineering file list; never discover local data."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def export_public(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    manifest = json.loads((root / "docs/public/release_manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    if len(files) != len(set(files)):
        raise ValueError("Duplicate release paths")
    sources = []
    for name in files:
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name:
            raise ValueError("Release path must be relative")
        source = root / relative
        if not source.resolve().is_relative_to(root) or any(parent.is_symlink() for parent in [source, *source.parents]):
            raise ValueError("Release paths must be regular workspace files")
        if not source.is_file():
            raise FileNotFoundError(name)
        sources.append((name, source))
    destination.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, source in sources:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "PUBLIC_CHECKSUMS.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    return {"files": len(files), "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_public(Path(__file__).resolve().parents[1], args.output), indent=2))


if __name__ == "__main__":
    main()
