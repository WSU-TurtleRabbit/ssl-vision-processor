from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
_PROTO_SRC = _REPO_ROOT / "proto"
_PROTO_OUT = _BACKEND_DIR / "proto"
# Repo-local protoc (>= 3.19, supports --pyi_out). The system `protoc` on
# Ubuntu 22.04 / JetPack is 3.12, whose _pb2.py output can't be imported by
# the protobuf 7.x runtime ("Descriptors cannot be created directly").
_LOCAL_PROTOC = _REPO_ROOT / ".protoc" / "bin" / "protoc"


def _run_protoc(protoc: str, args: list[str]) -> None:
    cmd_base = [protoc, f"--proto_path={_REPO_ROOT}", f"--python_out={_BACKEND_DIR}"]
    try:
        # Try generating .pyi stubs as well (protoc >= 3.20).
        subprocess.run([*cmd_base, f"--pyi_out={_BACKEND_DIR}", *args], check=True)
    except subprocess.CalledProcessError:
        # Older protoc doesn't support --pyi_out; generate bindings only.
        subprocess.run([*cmd_base, *args], check=True)


def _generate_proto_bindings() -> None:
    sources = sorted(_PROTO_SRC.glob("*.proto"))
    if not sources:
        raise RuntimeError(f"no .proto files found in {_PROTO_SRC}")
    print("Compiling Protobuf files...", file=sys.stderr)
    args = [str(p.relative_to(_REPO_ROOT)) for p in sources]

    # 1. Repo-local protoc, if present.
    if os.access(_LOCAL_PROTOC, os.X_OK):
        _run_protoc(str(_LOCAL_PROTOC), args)
        return

    # 2. grpc_tools.protoc (bundles its own protoc matching a recent runtime).
    try:
        from grpc_tools import protoc as _grpc_protoc  # type: ignore
    except ImportError:
        pass
    else:
        argv = ["protoc", f"-I{_REPO_ROOT}", f"--python_out={_BACKEND_DIR}", *args]
        if _grpc_protoc.main(argv) == 0:
            return

    # 3. Whatever `protoc` is on PATH.
    _run_protoc("protoc", args)


def _bindings_are_stale() -> bool:
    outputs = list(_PROTO_OUT.glob("*_pb2.py"))
    if not outputs:
        return True
    oldest_out = min(o.stat().st_mtime for o in outputs)
    newest_src = max(s.stat().st_mtime for s in _PROTO_SRC.glob("*.proto"))
    return newest_src > oldest_out


def _bindings_import() -> bool:
    # Checked in a subprocess so a failed import can't leave half-registered
    # descriptors or `proto.*` modules behind in this interpreter.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import proto.ssl_vision_wrapper_pb2, proto.ssl_vp_config_pb2"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(_BACKEND_DIR)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


if _bindings_are_stale() or not _bindings_import():
    _generate_proto_bindings()
    if not _bindings_import():
        shutil.rmtree(_PROTO_OUT, ignore_errors=True)
        raise RuntimeError(
            "generated protobuf bindings fail to import with the installed "
            "protobuf runtime; protoc >= 3.19 is required. Install one at "
            f"{_LOCAL_PROTOC} (or `uv pip install grpcio-tools`), then restart."
        )

# Generated _pb2.py files import siblings as `from proto import X_pb2`,
# so the directory holding them must be on sys.path as `proto`.
sys.path.insert(0, str(_BACKEND_DIR))
