from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from app.services.deadline import DeadlineExceededError, clamp_timeout_seconds


class WorkspaceError(RuntimeError):
    pass


ALLOWED_COMMANDS = {"python", "python3", "pytest", "ruff", "mypy", "npm", "node", "php", "composer"}
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", "dist", "build"}
TEXT_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".md", ".txt", ".toml", ".yaml", ".yml",
    ".html", ".css", ".scss", ".php", ".sql", ".sh", ".ini", ".cfg", ".xml", ".vue", ".go", ".rs",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def workspace_root(storage_root: str, workspace_id: str) -> Path:
    root = (Path(storage_root).resolve() / workspace_id).resolve()
    base = Path(storage_root).resolve()
    if root.parent != base:
        raise WorkspaceError("Invalid workspace root")
    return root


def safe_relative_path(value: str) -> PurePosixPath:
    raw = value.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WorkspaceError("Unsafe workspace path")
    if path.parts[0] in {".git", ".ssh", ".gnupg"}:
        raise WorkspaceError("Protected workspace path")
    return path


def resolve_inside(root: Path, relative: str) -> Path:
    rel = safe_relative_path(relative)
    target = (root / Path(*rel.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspaceError("Path escapes workspace") from exc
    return target


def write_text(root: Path, relative: str, content: str, expected_sha256: str | None = None) -> dict:
    target = resolve_inside(root, relative)
    old_sha = sha256_file(target) if target.is_file() else None
    if expected_sha256 is not None and old_sha != expected_sha256:
        raise WorkspaceError("File changed since plan was created")
    if target.exists() and not target.is_file():
        raise WorkspaceError("Target is not a regular file")
    data = content.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".x1tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return {"path": relative, "before_sha256": old_sha, "after_sha256": sha256_bytes(data), "bytes": len(data)}


def import_zip(root: Path, data: bytes, *, max_files: int, max_unpacked_bytes: int) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    tmp_zip = root.parent / f"{root.name}.upload.zip"
    tmp_dir = root.parent / f"{root.name}.importing"
    tmp_zip.write_bytes(data)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    try:
        with zipfile.ZipFile(tmp_zip) as archive:
            infos = archive.infolist()
            if len(infos) > max_files:
                raise WorkspaceError("Archive contains too many entries")
            for info in infos:
                if info.is_dir():
                    continue
                count += 1
                total += int(info.file_size)
                if total > max_unpacked_bytes:
                    raise WorkspaceError("Archive expands beyond workspace limit")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise WorkspaceError("Symlinks are not allowed in workspace archives")
                rel = safe_relative_path(info.filename)
                target = resolve_inside(tmp_dir, str(rel))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        shutil.rmtree(root, ignore_errors=True)
        os.replace(tmp_dir, root)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        tmp_zip.unlink(missing_ok=True)
    return {"file_count": count, "total_bytes": total}


def repo_map(root: Path, *, max_files: int = 1500) -> dict:
    files: list[dict] = []
    total = 0
    if not root.exists():
        return {"files": [], "file_count": 0, "total_bytes": 0}
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        item = {"path": path.relative_to(root).as_posix(), "bytes": size}
        if path.suffix.lower() in TEXT_EXTS and size <= 2_000_000:
            item["sha256"] = sha256_file(path)
        files.append(item)
        if len(files) >= max_files:
            break
    return {"files": files, "file_count": len(files), "total_bytes": total}


def path_allowed(path: str, allowed_paths: list[str]) -> bool:
    rel = safe_relative_path(path).as_posix()
    if not allowed_paths:
        return True
    for allowed in allowed_paths:
        base = safe_relative_path(allowed).as_posix().rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            return True
    return False


def _validated_static_command(root: Path, argv: list[str]) -> bool:
    exe = Path(argv[0]).name
    if exe not in {"python", "python3"} or len(argv) < 4 or argv[1:3] != ["-m", "py_compile"]:
        return False
    for value in argv[3:]:
        if value.startswith("-"):
            return False
        target = resolve_inside(root, value)
        if not target.is_file() or target.suffix.lower() != ".py":
            return False
    return True


def run_command(root: Path, argv: list[str], timeout_seconds: int, *, allow_unsafe: bool = False) -> dict:
    if not argv:
        raise WorkspaceError("Empty command")
    exe = Path(argv[0]).name
    if exe not in ALLOWED_COMMANDS:
        raise WorkspaceError("Command is not allowed")
    if any("\x00" in value for value in argv):
        raise WorkspaceError("Invalid command argument")

    static_safe = _validated_static_command(root, argv)
    if not static_safe and not allow_unsafe:
        raise WorkspaceError("Command requires an isolated sandbox backend")

    try:
        effective_timeout = clamp_timeout_seconds(float(timeout_seconds), stage="workspace verification command", minimum=.05)
    except DeadlineExceededError as exc:
        raise WorkspaceError("Request deadline exceeded before verification command") from exc

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(root / ".x1home"),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "",
    }
    Path(env["HOME"]).mkdir(exist_ok=True)
    sandbox_level = "static_py_compile" if static_safe else "operator_opt_in_host"
    try:
        completed = subprocess.run(
            argv,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=effective_timeout,
            shell=False,
        )
        return {
            "argv": argv,
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-20_000:],
            "stderr": completed.stderr[-20_000:],
            "timed_out": False,
            "sandbox_level": sandbox_level,
            "network_isolation": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "exit_code": None,
            "stdout": (exc.stdout or "")[-20_000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-20_000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
            "sandbox_level": sandbox_level,
            "network_isolation": False,
        }
