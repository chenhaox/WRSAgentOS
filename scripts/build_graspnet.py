"""Build pinned GraspNet CUDA extensions in a separate copy; leave model source unchanged."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "280c215129f759ed8649cb4e89fc5dfee55f4f80"


def build_environment(cuda):
    env = os.environ.copy()
    vswhere = (Path(os.environ["ProgramFiles(x86)"])
               / "Microsoft Visual Studio/Installer/vswhere.exe")
    install = subprocess.check_output(
        [str(vswhere), "-latest", "-products", "*", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        text=True, timeout=15,
    ).strip()
    if not install:
        raise RuntimeError("MSVC C++ tools are required")
    devcmd = Path(install) / "Common7/Tools/VsDevCmd.bat"
    output = subprocess.check_output(
        f'call "{devcmd}" -arch=x64 -host_arch=x64 >nul && set',
        shell=True, text=True, timeout=60,
    )
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            env[key.upper()] = value
    env.update(CUDA_HOME=str(cuda), CUDA_PATH=str(cuda), DISTUTILS_USE_SDK="1",
               MAX_JOBS="2", UV_CACHE_DIR=str(ROOT / ".local/uv-cache"))
    env["PATH"] = str(cuda / "bin") + os.pathsep + env["PATH"]
    return env


def compatible_source(text, *, knn):
    # Use only the stream/allocator headers needed by these kernels, not BLAS/Solver headers.
    text = text.replace("#include <ATen/cuda/CUDAContext.h>",
                        "#include <c10/cuda/CUDAStream.h>\n"
                        "#include <c10/cuda/CUDACachingAllocator.h>")
    text = text.replace("#include <ATen/cuda/CUDAEvent.h>", "")
    text = text.replace("at::cuda::getCurrentCUDAStream", "c10::cuda::getCurrentCUDAStream")
    text = text.replace(".type().is_cuda()", ".is_cuda()")
    text = text.replace(".data<", ".data_ptr<")
    text = text.replace("__PRETTY_FUNCTION__", "__FUNCTION__")
    if knn:
        # MSVC long is 32-bit even on x64; torch.long and the KNN output require int64.
        text = re.sub(r"\bunsigned long\b", "uint64_t", text)
        text = re.sub(r"\blong\b", "int64_t", text)
        text = "#include <cstdint>\n" + text
    return text


def prepare(source, destination):
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10,
    ).strip()
    if revision != REVISION:
        raise ValueError("graspnet_source_revision_mismatch")
    subprocess.run(["git", "-C", str(source), "diff", "--exit-code", "HEAD", "--"],
                   check=True, capture_output=True, timeout=10)
    # Only tracked compiler inputs are copied. Local or generated files cannot enter the build.
    paths = subprocess.check_output(
        ["git", "-C", str(source), "ls-files", "pointnet2/_ext_src", "knn/src"],
        text=True, timeout=10,
    ).splitlines()
    manifest = {}
    for name in paths:
        original = (source / name).read_text("utf-8")
        patched = compatible_source(original, knn=name.startswith("knn/"))
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(patched, encoding="utf-8", newline="\n")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    shutil.copyfile(source / "LICENSE", destination / "LICENSE")
    for package in ("pointnet2", "knn_pytorch"):
        path = destination / package
        path.mkdir(exist_ok=True)
        (path / "__init__.py").write_text("", encoding="utf-8")
    (destination / "build_sources.json").write_text(
        json.dumps({"revision": revision, "patched_sha256": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    # Separate package: do not execute either upstream setup.py or alter upstream Python files.
    (destination / "setup.py").write_text('''from pathlib import Path
from setuptools import setup
from torch.utils.cpp_extension import BuildExtension, CUDAExtension

root = Path(__file__).parent
pointnet = root / "pointnet2/_ext_src"
knn = root / "knn/src"
flags = {"cxx": ["/O2"], "nvcc": ["-O2"]}
setup(name="wrs-graspnet-extensions", version="0.1.0", packages=["pointnet2", "knn_pytorch"],
      ext_modules=[
          CUDAExtension("pointnet2._ext",
              [str(p.relative_to(root)) for p in sorted((pointnet / "src").glob("*"))
               if p.suffix in (".cpp", ".cu")],
              include_dirs=[str(pointnet / "include")], extra_compile_args=flags),
          CUDAExtension("knn_pytorch.knn_pytorch",
              [str(p.relative_to(root)) for p in sorted(knn.rglob("*"))
               if p.suffix in (".cpp", ".cu")],
              include_dirs=[str(knn)], define_macros=[("WITH_CUDA", None)],
              extra_compile_args=flags),
      ], cmdclass={"build_ext": BuildExtension})
''', encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cuda-home", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("This build recipe has only been prepared for Windows/MSVC")
    cuda = args.cuda_home.resolve(strict=True)
    if not (cuda / "bin/nvcc.exe").is_file():
        raise FileNotFoundError("CUDA toolkit nvcc.exe is required")
    destination = ROOT / ".local/build/graspnet-extensions"
    destination.mkdir(parents=True, exist_ok=True)
    prepare(args.source.resolve(strict=True), destination)
    env = build_environment(cuda)
    subprocess.run([str(ROOT / ".local/tools/bin/uv.exe"), "pip", "install", "--python",
                    sys.executable, "--no-deps", "--no-build-isolation", "--reinstall",
                    str(destination)], env=env, check=True)
    print(f"Built extensions from {REVISION}. Trained inference still requires weights.")


if __name__ == "__main__":
    main()
