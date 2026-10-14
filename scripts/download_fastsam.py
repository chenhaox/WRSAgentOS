"""Explicit download of the pinned FastSAM-x checkpoint; verify before atomic placement."""

import argparse
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "wrs_agent/nodes/segmentation/fastsam_assets.json"


def verify(path, entry):
    if path.stat().st_size != entry["size"]:
        raise ValueError("fastsam_weight_size_mismatch")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != entry["sha256"]:
        raise ValueError("fastsam_weight_checksum_mismatch")


def download(directory):
    entry = json.loads(MANIFEST.read_text("utf-8"))
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / entry["filename"]
    if destination.exists():
        verify(destination, entry)
        return destination
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "WRS-Agent-models"})
    temporary = None
    try:
        with urllib.request.urlopen(request, timeout=60) as source, tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix="FastSAM-x-", suffix=".part", delete=False,
        ) as output:
            temporary = Path(output.name)
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > entry["size"]:
                    raise ValueError("fastsam_download_too_large")
                output.write(chunk)
        verify(temporary, entry)
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, default=ROOT / ".local/models/fastsam")
    args = parser.parse_args()
    path = download(args.directory.resolve())
    entry = json.loads(MANIFEST.read_text("utf-8"))
    print(json.dumps({"weights": str(path), "bytes": entry["size"], "sha256": entry["sha256"],
                      "release": entry["release"], "verified": True}, indent=2))


if __name__ == "__main__":
    main()
