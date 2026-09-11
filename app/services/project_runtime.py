from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.code_workspace import WorkspaceError, repo_map


class RuntimeError(RuntimeError):
    pass


def runtime_root(storage_root: str, runtime_id: str) -> Path:
    base = Path(storage_root).resolve()
    root = (base / runtime_id).resolve()
    if root.parent != base:
        raise RuntimeError("Invalid runtime root")
    return root


def detect_isolation_backend() -> dict:
    unshare = shutil.which("unshare")
    if not unshare:
        return {"backend": "filesystem", "network_namespace": False, "user_namespace": False}
    try:
        cp = subprocess.run([unshare, "-Urn", "true"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        ok = cp.returncode == 0
    except Exception:
        ok = False
    return {"backend": "linux_namespace" if ok else "filesystem", "network_namespace": ok, "user_namespace": ok}


def build_manifest(root: Path, *, project_id: str, workspace_id: str, cpu_limit: float, memory_mb: int, disk_mb: int, process_limit: int, network_policy: str) -> dict:
    mapping = repo_map(root)
    return {
        "project_id": project_id,
        "workspace_id": workspace_id,
        "root": str(root),
        "limits": {"cpu": cpu_limit, "memory_mb": memory_mb, "disk_mb": disk_mb, "processes": process_limit},
        "network_policy": network_policy,
        "repo": {"file_count": mapping["file_count"], "total_bytes": mapping["total_bytes"]},
        "forbidden_mounts": ["x1_source", "docker_socket", "host_ssh", "other_project_roots"],
    }


def _key(secret: str) -> bytes:
    if not secret or secret == "change-me-runtime-secret":
        raise RuntimeError("Project runtime secret key is not configured")
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt_secret(value: str, secret: str) -> str:
    nonce = os.urandom(12)
    data = AESGCM(_key(secret)).encrypt(nonce, value.encode("utf-8"), b"x1-project-runtime")
    return base64.urlsafe_b64encode(nonce + data).decode("ascii")


def decrypt_secret(ciphertext: str, secret: str) -> str:
    raw = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
    if len(raw) < 13:
        raise RuntimeError("Invalid secret payload")
    return AESGCM(_key(secret)).decrypt(raw[:12], raw[12:], b"x1-project-runtime").decode("utf-8")


def create_snapshot(root: Path, snapshot_dir: Path) -> dict:
    mapping = repo_map(root, max_files=100000)
    manifest = {"files": mapping["files"], "file_count": mapping["file_count"], "total_bytes": mapping["total_bytes"]}
    digest = hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    archive = snapshot_dir / f"{digest}.tar.gz"
    if not archive.exists():
        tmp = archive.with_suffix(".tmp")
        with tarfile.open(tmp, "w:gz") as tf:
            for item in mapping["files"]:
                path = root / item["path"]
                if path.is_file() and not path.is_symlink():
                    tf.add(path, arcname=item["path"], recursive=False)
        os.replace(tmp, archive)
    return {"archive_path": str(archive), "manifest_sha256": digest, "manifest": manifest}
