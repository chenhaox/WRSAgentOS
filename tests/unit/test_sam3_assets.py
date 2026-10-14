import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from wrs_agent.nodes.segmentation import sam3


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / "model"
    directory.mkdir()
    weights = directory / "model.safetensors"
    weights.write_bytes(b"fixture-weights")
    config = b'{"fixture": true}'
    (directory / "config.json").write_bytes(config)
    config_hash = hashlib.sha1(f"blob {len(config)}\0".encode() + config).hexdigest()
    manifest = {
        "weights": weights.name, "weights_size": weights.stat().st_size,
        "files": [{"path": "config.json", "size": len(config),
                   "git_sha1": config_hash}],
    }
    (tmp_path / "assets.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(sam3, "files", lambda _: tmp_path)
    return weights, hashlib.sha256(weights.read_bytes()).hexdigest()


def test_local_bundle_checks_weights_and_processor_files(bundle):
    weights, digest = bundle
    assert sam3.model_directory(weights, digest) == weights.parent


@pytest.mark.parametrize("change", ["hash", "size", "missing", "sidecar", "weights_name"])
def test_changed_bundle_rejected_before_loading_sdk(bundle, change):
    weights, digest = bundle
    config = weights.parent / "config.json"
    expected = ValueError
    if change == "hash":
        config.write_bytes(b"x" * config.stat().st_size)
        error = "hash_mismatch"
    elif change == "size":
        config.write_bytes(b"{}")
        error = "size_mismatch"
    elif change == "missing":
        config.unlink()
        expected, error = FileNotFoundError, "config.json"
    elif change == "sidecar":
        (weights.parent / "added_tokens.json").write_text("{}")
        error = "unverified_sidecar"
    else:
        weights = weights.rename(weights.with_name("sam3.pt"))
        error = "pinned_safetensors_bundle"
    with pytest.raises(expected, match=error):
        sam3.Sam3Segmenter(weights, digest)


@pytest.mark.parametrize("token", [None, "fixture-token"])
def test_download_requires_explicit_credentials_and_visible_upstream_hash(tmp_path, monkeypatch,
                                                                         token):
    from scripts.download_sam3_model import download

    if token:
        monkeypatch.setenv("HF_TOKEN", token)
    else:
        monkeypatch.delenv("HF_TOKEN", raising=False)
    entry = json.loads(sam3.files(sam3.__package__).joinpath("assets.json").read_text("utf-8"))

    class API:
        def __init__(self, *, token):
            assert token == "fixture-token"

        def model_info(self, repo, *, revision, files_metadata):
            assert repo == entry["repo"] and revision == entry["revision"] and files_metadata
            return SimpleNamespace(sha=revision, siblings=[SimpleNamespace(
                rfilename=entry["weights"], size=entry["weights_size"],
                lfs=SimpleNamespace(sha256="*" * 64),
            )])

    def forbidden_download(*args, **kwargs):
        pytest.fail("Download attempted without a trusted upstream checksum")

    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        SimpleNamespace(HfApi=API, hf_hub_download=forbidden_download))
    with pytest.raises(ValueError, match="SHA256 unavailable" if token else "Set HF_TOKEN"):
        download(tmp_path)
