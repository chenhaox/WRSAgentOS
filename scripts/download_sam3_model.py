"""Explicit SAM3 download: approved HF_TOKEN, pinned revision and upstream checksums."""

import argparse
import json
import os
import re
from importlib.resources import files
from pathlib import Path

from wrs_agent.nodes.segmentation.sam3 import model_directory


def download(directory):
    from huggingface_hub import HfApi, hf_hub_download

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise ValueError("Set HF_TOKEN after obtaining facebook/sam3 access; no cached login")
    entry = json.loads(files("wrs_agent.nodes.segmentation").joinpath("assets.json")
                       .read_text("utf-8"))
    info = HfApi(token=token).model_info(entry["repo"], revision=entry["revision"],
                                       files_metadata=True)
    weight = next(item for item in info.siblings if item.rfilename == entry["weights"])
    digest = weight.lfs.sha256 if weight.lfs else ""
    if (info.sha != entry["revision"] or weight.size != entry["weights_size"]
            or not re.fullmatch(r"[a-f0-9]{64}", digest)):
        raise ValueError("Official weight revision/size/SHA256 unavailable; nothing downloaded")
    for name in [entry["weights"], *(item["path"] for item in entry["files"])]:
        hf_hub_download(entry["repo"], name, revision=entry["revision"],
                        local_dir=directory, token=token)
    model_directory(directory / entry["weights"], digest)
    return {"repo": entry["repo"], "revision": entry["revision"],
            "weights": str((directory / entry["weights"]).resolve()), "sha256": digest,
            "verified": True, "inference": "not_run"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True,
                        help="Explicitly download ~3.45 GB after accepting Meta's license")
    parser.add_argument("--directory", type=Path,
                        default=Path(__file__).resolve().parents[1] / ".local/models/sam3")
    args = parser.parse_args()
    print(json.dumps(download(args.directory.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
