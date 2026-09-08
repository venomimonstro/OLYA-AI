from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


class GitError(RuntimeError):
    pass


_MAX_OUTPUT_CHARS = 200_000
_MAX_SCAN_BYTES = 5 * 1024 * 1024
_GITHUB_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GITHUB_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("generic_secret", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*['\"]?([^\s'\"#]{12,})")),
)
_PLACEHOLDER_SECRET = re.compile(r"(?i)^(?:change-me|example|placeholder|dummy|test|your[_-]|xxx+|<.*>|\$\{.*\})")


def _validate_branch(value: str) -> str:
    branch = value.strip()
    if not branch or len(branch) > 160:
        raise GitError("Invalid Git branch")
    if branch.startswith(("-", "/", ".")) or branch.endswith(("/", ".", ".lock")):
        raise GitError("Invalid Git branch")
    if any(token in branch for token in ("..", "@{", "\\", " ", "~", "^", ":", "?", "*", "[")):
        raise GitError("Invalid Git branch")
    if any(ord(char) < 32 or ord(char) == 127 for char in branch) or "//" in branch or "/." in branch:
        raise GitError("Invalid Git branch")
    return branch


def _safe_path(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GitError("Unsafe Git path")
    if path.parts[0] in {".git", ".ssh", ".gnupg"}:
        raise GitError("Protected Git path")
    return path.as_posix()


def _repo_root(root: Path, *, require_git: bool = True) -> Path:
    candidate = root.expanduser()
    if candidate.is_symlink():
        raise GitError("Workspace root cannot be a symlink")
    candidate = candidate.resolve()
    if require_git:
        marker = candidate / ".git"
        # X1 does not need linked worktrees/submodules as workspace roots. A
        # regular .git pointer file can redirect Git metadata outside the
        # workspace, so require the metadata directory to be physically local.
        if marker.is_symlink() or not marker.is_dir():
            raise GitError("Workspace Git metadata must be a local directory")
    return candidate


def normalize_github_url(value: str) -> tuple[str, str, str]:
    raw = value.strip()
    if raw.startswith("git@github.com:"):
        path = raw[len("git@github.com:"):]
    else:
        parsed = urlparse(raw)
        if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != "github.com":
            raise GitError("Only HTTPS github.com repository URLs are allowed")
        try:
            port = parsed.port
        except ValueError as exc:
            raise GitError("Invalid GitHub repository URL port") from exc
        if parsed.username or parsed.password or parsed.query or parsed.fragment or port not in {None, 443}:
            raise GitError("GitHub repository URL cannot contain credentials, query parameters or a custom port")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2:
        raise GitError("GitHub repository URL must contain exactly owner/repository")
    owner, name = parts
    if not _GITHUB_OWNER.fullmatch(owner) or not _GITHUB_REPO.fullmatch(name) or name in {".", ".."}:
        raise GitError("Invalid GitHub owner or repository name")
    return f"https://github.com/{owner}/{name}.git", owner, name


def _redact(text: str, token: str = "", auth_header: str = "") -> str:
    result = text or ""
    for secret in (token, auth_header):
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result[-_MAX_OUTPUT_CHARS:]


@contextmanager
def _git_env(token: str = ""):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("GIT_"):
            env.pop(key, None)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
        "GIT_EDITOR": "true",
        "GIT_SEQUENCE_EDITOR": "true",
        "GIT_EXTERNAL_DIFF": "",
        "LC_ALL": "C.UTF-8",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "github.com",
    })
    config_path = ""
    auth_header = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="olya-git-", suffix=".config", delete=False) as handle:
            config_path = handle.name
            handle.write("[credential]\n\thelper =\n")
            if token:
                basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
                auth_header = f"Authorization: Basic {basic}"
                handle.write('[http "https://github.com/"]\n')
                handle.write(f"\textraHeader = {auth_header}\n")
        os.chmod(config_path, 0o600)
        env["GIT_CONFIG_GLOBAL"] = config_path
        yield env, auth_header
    finally:
        if config_path:
            try:
                os.unlink(config_path)
            except FileNotFoundError:
                pass


def _run_git(cwd: Path, args: list[str], *, token: str = "", timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    argv = [
        "git",
        "-c", "core.hooksPath=/dev/null",
        "-c", "commit.gpgSign=false",
        "-c", "protocol.file.allow=never",
        "-c", "protocol.ext.allow=never",
        *args,
    ]
    with _git_env(token) as (env, auth_header):
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f"Git command failed: {type(exc).__name__}") from exc
    if check and completed.returncode != 0:
        detail = _redact(completed.stderr or completed.stdout, token, auth_header).strip()
        raise GitError(detail or f"Git command failed with exit code {completed.returncode}")
    return completed


def _identity(root: Path) -> None:
    name = _run_git(root, ["config", "--get", "user.name"], check=False)
    if name.returncode != 0 or not name.stdout.strip():
        _run_git(root, ["config", "user.name", "OLYA AI"])
    email = _run_git(root, ["config", "--get", "user.email"], check=False)
    if email.returncode != 0 or not email.stdout.strip():
        _run_git(root, ["config", "user.email", "olya-ai@localhost"])


def head(root: Path) -> str:
    repo = _repo_root(root)
    result = _run_git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    value = result.stdout.strip().lower() if result.returncode == 0 else ""
    return value if _SHA.fullmatch(value) else ""


def changed_paths(root: Path) -> list[str]:
    repo = _repo_root(root)
    result = _run_git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    tokens = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2]
        candidate = record[3:]
        if candidate:
            try:
                paths.append(_safe_path(candidate))
            except GitError:
                pass
        if "R" in status or "C" in status:
            if index < len(tokens) and tokens[index]:
                try:
                    paths.append(_safe_path(tokens[index]))
                except GitError:
                    pass
                index += 1
    return sorted(set(paths))


def git_status(root: Path) -> dict:
    repo = _repo_root(root)
    branch_result = _run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    paths = changed_paths(repo)
    return {"branch": branch, "head": head(repo), "dirty": bool(paths), "changed_paths": paths}


def git_diff(root: Path, *, staged: bool = False) -> dict:
    repo = _repo_root(root)
    args = ["diff", "--no-ext-diff", "--no-color"]
    if staged:
        args.append("--cached")
    result = _run_git(repo, args)
    full = result.stdout
    return {"staged": bool(staged), "diff": full[:_MAX_OUTPUT_CHARS], "truncated": len(full) > _MAX_OUTPUT_CHARS}


def ensure_local_repo(root: Path, default_branch: str) -> dict:
    branch = _validate_branch(default_branch)
    repo = _repo_root(root, require_git=False)
    repo.mkdir(parents=True, exist_ok=True)
    marker = repo / ".git"
    if marker.exists():
        if marker.is_symlink() or not marker.is_dir():
            raise GitError("Git metadata must be a directory inside the workspace")
    else:
        _run_git(repo, ["init", "--initial-branch", branch])
    _identity(repo)
    return git_status(repo)


def set_origin(root: Path, repository_url: str) -> None:
    repo = _repo_root(root)
    canonical, _, _ = normalize_github_url(repository_url)
    current = _run_git(repo, ["remote", "get-url", "origin"], check=False)
    if current.returncode == 0:
        _run_git(repo, ["remote", "set-url", "origin", canonical])
    else:
        _run_git(repo, ["remote", "add", "origin", canonical])


def checkout_branch(root: Path, branch: str, *, base_branch: str = "main") -> dict:
    repo = _repo_root(root)
    target = _validate_branch(branch)
    base = _validate_branch(base_branch)
    local = _run_git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{target}"], check=False)
    if local.returncode == 0:
        _run_git(repo, ["checkout", target])
        return git_status(repo)
    base_ref = ""
    for candidate in (f"refs/heads/{base}", f"refs/remotes/origin/{base}"):
        probe = _run_git(repo, ["show-ref", "--verify", "--quiet", candidate], check=False)
        if probe.returncode == 0:
            base_ref = candidate
            break
    _run_git(repo, ["checkout", "-b", target, base_ref] if base_ref else ["checkout", "-b", target])
    return git_status(repo)


def clone_into_workspace(root: Path, repository_url: str, default_branch: str, token: str) -> dict:
    canonical, _, _ = normalize_github_url(repository_url)
    branch = _validate_branch(default_branch)
    destination = _repo_root(root, require_git=False)
    if destination.exists() and any(destination.iterdir()):
        raise GitError("Workspace must be empty before clone")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{destination.name}.clone-", dir=destination.parent))
    try:
        _run_git(destination.parent, ["clone", "--no-tags", "--single-branch", "--branch", branch, canonical, str(temp_dir)], token=token, timeout=300)
        _identity(temp_dir)
        if destination.exists():
            destination.rmdir()
        os.replace(temp_dir, destination)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return git_status(destination)


def remote_head(root: Path, branch: str, token: str) -> str:
    repo = _repo_root(root)
    target = _validate_branch(branch)
    result = _run_git(repo, ["ls-remote", "--heads", "origin", f"refs/heads/{target}"], token=token)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == f"refs/heads/{target}":
            value = parts[0].lower()
            if _SHA.fullmatch(value):
                return value
    return ""


def fetch(root: Path, branch: str, token: str) -> dict:
    repo = _repo_root(root)
    target = _validate_branch(branch)
    before = remote_head(repo, target, token)
    _run_git(repo, ["fetch", "--no-tags", "--prune", "origin", f"refs/heads/{target}:refs/remotes/origin/{target}"], token=token, timeout=300)
    result = _run_git(repo, ["rev-parse", "--verify", f"refs/remotes/origin/{target}"], check=False)
    after = result.stdout.strip().lower() if result.returncode == 0 else ""
    if after and not _SHA.fullmatch(after):
        after = ""
    return {"branch": target, "remote_head_before": before, "remote_head_after": after, "local_head": head(repo)}


def push_candidate_paths(root: Path, branch: str, token: str) -> tuple[str, list[str]]:
    repo = _repo_root(root)
    target = _validate_branch(branch)
    before = remote_head(repo, target, token)
    if before:
        _run_git(repo, ["fetch", "--no-tags", "origin", f"refs/heads/{target}:refs/remotes/origin/{target}"], token=token, timeout=300)
        result = _run_git(repo, ["diff", "--name-only", "--diff-filter=ACMR", f"refs/remotes/origin/{target}..HEAD", "--"])
    else:
        result = _run_git(repo, ["ls-files"])
    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.strip():
            try:
                paths.append(_safe_path(line.strip()))
            except GitError:
                pass
    return before, sorted(set(paths))


def _text_from_head(root: Path, path: str) -> str | None:
    result = _run_git(root, ["show", f"HEAD:{path}"], check=False)
    if result.returncode != 0 or len(result.stdout.encode("utf-8", errors="ignore")) > _MAX_SCAN_BYTES:
        return None
    return result.stdout


def _scan_text(path: str, text: str, origin: str) -> list[dict]:
    findings: list[dict] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in _SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if kind == "generic_secret" and _PLACEHOLDER_SECRET.match(match.group(1).strip()):
                continue
            findings.append({"path": path, "line": number, "kind": kind, "origin": origin})
            break
    return findings


def scan_secrets(root: Path, paths: list[str]) -> list[dict]:
    repo = _repo_root(root)
    findings: list[dict] = []
    for raw in sorted(set(paths)):
        path = _safe_path(raw)
        target = (repo / path).resolve()
        try:
            target.relative_to(repo)
        except ValueError:
            findings.append({"path": path, "line": 0, "kind": "unsafe_path", "origin": "workspace"})
            continue
        if target.is_symlink():
            findings.append({"path": path, "line": 0, "kind": "symlink_not_scanned", "origin": "workspace"})
            continue
        if target.is_file() and target.stat().st_size <= _MAX_SCAN_BYTES:
            data = target.read_bytes()
            if b"\x00" not in data:
                findings.extend(_scan_text(path, data.decode("utf-8", errors="replace"), "workspace"))
        committed = _text_from_head(repo, path)
        if committed is not None:
            findings.extend(_scan_text(path, committed, "HEAD"))
    unique = {(item["path"], item["line"], item["kind"], item["origin"]): item for item in findings}
    return list(unique.values())


def commit(root: Path, message: str, paths: list[str], *, expected_head: str = "") -> dict:
    repo = _repo_root(root)
    clean_message = message.strip()
    if len(clean_message) < 3 or len(clean_message) > 300 or "\x00" in clean_message:
        raise GitError("Invalid commit message")
    before = head(repo)
    if expected_head and before != expected_head.lower():
        raise GitError("Local Git HEAD changed since the operation was planned")
    selected = [_safe_path(path) for path in paths]
    _run_git(repo, ["add", "-A", "--", *selected] if selected else ["add", "-A", "--", "."])
    diff = _run_git(repo, ["diff", "--cached", "--quiet"], check=False)
    if diff.returncode == 0:
        raise GitError("Nothing to commit")
    if diff.returncode != 1:
        raise GitError("Unable to inspect staged Git changes")
    _identity(repo)
    _run_git(repo, ["commit", "--no-gpg-sign", "-m", clean_message], timeout=180)
    after = head(repo)
    if not after or after == before:
        raise GitError("Git commit did not advance HEAD")
    changed = _run_git(repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", after]).stdout.splitlines()
    return {"head_before": before, "head_after": after, "paths": [path for path in changed if path]}


def push(root: Path, branch: str, token: str, *, expected_head: str = "", expected_remote_head: str = "") -> dict:
    repo = _repo_root(root)
    target = _validate_branch(branch)
    local = head(repo)
    if not local:
        raise GitError("Cannot push an unborn Git repository")
    if expected_head and local != expected_head.lower():
        raise GitError("Local Git HEAD changed since push was planned")
    before = remote_head(repo, target, token)
    if expected_remote_head and before != expected_remote_head.lower():
        raise GitError("Remote branch changed since push was planned")
    _run_git(repo, ["push", "--porcelain", "origin", f"HEAD:refs/heads/{target}"], token=token, timeout=300)
    after = remote_head(repo, target, token)
    if after != local:
        raise GitError("Remote HEAD does not match the pushed local commit")
    return {"branch": target, "local_head": local, "remote_head_before": before, "remote_head_after": after}
