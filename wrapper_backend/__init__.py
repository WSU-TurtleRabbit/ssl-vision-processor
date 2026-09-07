from __future__ import annotations

import pathlib
import subprocess
import sys

_BACKEND_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _BACKEND_DIR.parent
_PROTO_SRC = _REPO_ROOT / "proto"
_PROTO_OUT = _BACKEND_DIR / "proto"


def _generate_proto_bindings() -> None:
    sources = sorted(_PROTO_SRC.glob("*.proto"))
    if not sources:
        raise RuntimeError(f"no .proto files found in {_PROTO_SRC}")
    print("Compiling Protobuf files...", file=sys.stderr)
    cmd_base = ["protoc", f"--proto_path={_REPO_ROOT}", f"--python_out={_BACKEND_DIR}"]
    args = [str(p.relative_to(_REPO_ROOT)) for p in sources]

    # Prefer using the project's venv grpc_tools.protoc (pure-Python) when
    # available. This avoids using an older system `protoc` that can generate
    # _pb2.py files incompatible with the installed `protobuf` runtime.
    # If we're running inside the project's venv and have grpc_tools
    # available, call it programmatically to avoid spawning a separate
    # process and to ensure the generated code matches the installed
    # protobuf runtime.
    try:
        from grpc_tools import protoc as _grpc_protoc  # type: ignore
        argv = [
            "protoc",
            f"-I{_REPO_ROOT}",
            f"--python_out={_BACKEND_DIR}",
            *args,
        ]
        # grpc_tools.protoc.main returns 0 on success
        rc = _grpc_protoc.main(argv)
        if rc == 0:
            return
        # fall through to system protoc if grpc_tools.protoc failed
    except Exception:
        # grpc_tools not available or failed; fall back to system protoc
        pass

    try:
        # Try generating .pyi stubs as well (requires protoc-gen-pyi plugin).
        subprocess.run([*cmd_base, f"--pyi_out={_BACKEND_DIR}", *args], check=True)
    except subprocess.CalledProcessError:
        # Older protoc (or missing plugin) doesn't support --pyi_out; fall back
        # to generating Python bindings only using system protoc.
        subprocess.run([*cmd_base, *args], check=True)


def _bindings_are_stale() -> bool:
    outputs = list(_PROTO_OUT.glob("*_pb2.py"))
    if not outputs:
        return True
    oldest_out = min(o.stat().st_mtime for o in outputs)
    newest_src = max(s.stat().st_mtime for s in _PROTO_SRC.glob("*.proto"))
    return newest_src > oldest_out


if _bindings_are_stale():
    _generate_proto_bindings()

# Generated _pb2.py files import siblings as `from proto import X_pb2`,
# so the directory holding them must be on sys.path as `proto`.
sys.path.insert(0, str(_BACKEND_DIR))
