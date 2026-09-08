from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxBackendInfo:
    backend: str
    executable: str | None
    available: bool
    reason: str = ""


def _remote_url() -> str:
    return os.environ.get("X1_PROJECT_SANDBOX_WORKER_URL", "http://sandbox-worker:8090").rstrip("/")


def _remote_token() -> str:
    return os.environ.get("X1_PROJECT_SANDBOX_WORKER_TOKEN", "")


def _remote_headers() -> dict[str, str]:
    token = _remote_token()
    if not token or token == "change-me-sandbox-worker":
        raise SandboxError("Sandbox worker token is not configured")
    return {"X-X1-Sandbox-Token": token, "Accept": "application/json"}


def _remote_request(path: str, *, payload: dict | None = None, method: str = "POST", timeout: float = 15.0) -> dict:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.request(method, f"{_remote_url()}{path}", headers=_remote_headers(), json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SandboxError(f"Sandbox worker request failed: {type(exc).__name__}") from exc


def _data_relative(path: Path) -> str:
    root = Path(os.environ.get("X1_DATA_ROOT", "/app/data")).resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise SandboxError("Sandbox path is outside X1 data root") from exc


def detect_container_backend(preferred: str = "auto") -> SandboxBackendInfo:
    if preferred == "remote":
        try:
            caps = _remote_request("/capabilities", method="GET", timeout=8)
        except SandboxError as exc:
            return SandboxBackendInfo("remote", None, False, str(exc))
        return SandboxBackendInfo("remote", None, bool(caps.get("available")), str(caps.get("reason") or ""))
    names = [preferred] if preferred in {"docker", "podman"} else ["docker", "podman"]
    for name in names:
        executable = shutil.which(name)
        if not executable:
            continue
        try:
            completed = subprocess.run([executable, "version"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, shell=False)
            if completed.returncode == 0:
                return SandboxBackendInfo(name, executable, True)
        except (OSError, subprocess.SubprocessError):
            continue
    return SandboxBackendInfo("unavailable", None, False, "Docker/Podman runtime is not available")


def _image_exists(info: SandboxBackendInfo, image: str) -> bool:
    if info.backend == "remote":
        try:
            caps = _remote_request("/capabilities", method="GET", timeout=8)
            return bool(caps.get("available") and caps.get("image") == image and caps.get("image_present"))
        except SandboxError:
            return False
    if not info.available or not info.executable or not image:
        return False
    argv = [info.executable, "image", "inspect", image] if info.backend == "docker" else [info.executable, "image", "exists", image]
    try:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, shell=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def sandbox_capabilities(preferred: str, image: str) -> dict:
    if preferred == "remote":
        try:
            caps = _remote_request("/capabilities", method="GET", timeout=8)
            if caps.get("image") != image:
                caps = {**caps, "available": False, "reason": "Configured sandbox image does not match worker image"}
            return caps
        except SandboxError as exc:
            return {"backend":"remote","available":False,"runtime_available":False,"image":image,"image_present":False,"network_isolation":False,"restricted_network_mode":"deny","filesystem_isolation":False,"reason":str(exc)}
    info = detect_container_backend(preferred)
    present = _image_exists(info, image) if info.available else False
    available = bool(info.available and present)
    return {"backend":info.backend,"available":available,"runtime_available":info.available,"image":image,"image_present":present,"network_isolation":available,"restricted_network_mode":"deny_until_egress_allowlist_is_configured","filesystem_isolation":available,"reason":"" if available else ("Sandbox image is not installed locally" if info.available else info.reason)}


def _base_run_args(info: SandboxBackendInfo, *, image: str, workspace: Path, scratch: Path, cpu_limit: float, memory_mb: int, process_limit: int, network_policy: str, read_only_workspace: bool=False) -> list[str]:
    if not info.available or not info.executable:
        raise SandboxError("Container sandbox runtime is unavailable")
    if not _image_exists(info, image):
        raise SandboxError("Configured sandbox image is not installed locally")
    if network_policy not in {"deny", "restricted"}:
        raise SandboxError("Unsupported network policy")
    workspace=workspace.resolve(); scratch=scratch.resolve(); workspace.mkdir(parents=True,exist_ok=True); scratch.mkdir(parents=True,exist_ok=True)
    mount_mode="ro" if read_only_workspace else "rw"
    argv=[info.executable,"run","--rm","--pull=never","--workdir","/workspace","--read-only","--cap-drop=ALL","--security-opt","no-new-privileges","--pids-limit",str(max(16,int(process_limit))),"--memory",f"{max(128,int(memory_mb))}m","--cpus",str(max(.1,float(cpu_limit))),"--mount",f"type=bind,src={workspace},dst=/workspace,{mount_mode}","--mount",f"type=bind,src={scratch},dst=/x1-runtime,rw","--tmpfs","/tmp:rw,noexec,nosuid,nodev,size=256m","--network","none"]
    if hasattr(os,"getuid") and hasattr(os,"getgid"):
        argv += ["--user",f"{os.getuid()}:{os.getgid()}"]
    argv.append(image)
    return argv


def run_in_container(*, preferred_backend: str, image: str, workspace: Path, scratch: Path, argv: list[str], timeout_seconds: int, cpu_limit: float, memory_mb: int, process_limit: int, network_policy: str, env: dict[str,str]|None=None, read_only_workspace: bool=False) -> dict:
    if not argv or any("\x00" in value for value in argv):
        raise SandboxError("Invalid sandbox command")
    if preferred_backend == "remote":
        return _remote_request("/execute", payload={"image":image,"workspace_rel":_data_relative(workspace),"scratch_rel":_data_relative(scratch),"argv":argv,"timeout_seconds":max(1,int(timeout_seconds)),"cpu_limit":max(.1,float(cpu_limit)),"memory_mb":max(128,int(memory_mb)),"process_limit":max(16,int(process_limit)),"network_policy":network_policy,"env":env or {},"read_only_workspace":bool(read_only_workspace)}, timeout=max(15.0,float(timeout_seconds)+10))
    info=detect_container_backend(preferred_backend); command=_base_run_args(info,image=image,workspace=workspace,scratch=scratch,cpu_limit=cpu_limit,memory_mb=memory_mb,process_limit=process_limit,network_policy=network_policy,read_only_workspace=read_only_workspace)
    for key,value in sorted((env or {}).items()):
        if not key.replace("_","").isalnum() or key.upper()!=key: raise SandboxError("Invalid sandbox environment variable name")
        command += ["--env",f"{key}={value}"]
    command += argv
    try:
        completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=max(1,int(timeout_seconds)),shell=False)
        return {"argv":argv,"exit_code":completed.returncode,"stdout":completed.stdout[-30000:],"stderr":completed.stderr[-30000:],"timed_out":False,"sandbox_level":info.backend,"network_policy":network_policy,"effective_network_policy":"deny","read_only_workspace":bool(read_only_workspace)}
    except subprocess.TimeoutExpired as exc:
        return {"argv":argv,"exit_code":None,"stdout":(exc.stdout or "")[-30000:] if isinstance(exc.stdout,str) else "","stderr":(exc.stderr or "")[-30000:] if isinstance(exc.stderr,str) else "","timed_out":True,"sandbox_level":info.backend,"network_policy":network_policy,"effective_network_policy":"deny","read_only_workspace":bool(read_only_workspace)}


def sanitize_health_spec(spec: dict|None) -> dict:
    spec=dict(spec or {}); path=str(spec.get("path") or "/health")
    if not path.startswith("/") or "//" in path or len(path)>300: raise SandboxError("Invalid health path")
    expected=int(spec.get("expected_status") or 200)
    if expected<100 or expected>599: raise SandboxError("Invalid expected health status")
    return {"path":path,"expected_status":expected}


def start_detached_preview(*, preferred_backend: str, image: str, workspace: Path, scratch: Path, argv: list[str], name: str, cpu_limit: float, memory_mb: int, process_limit: int, network_policy: str) -> dict:
    if not argv: raise SandboxError("Preview command is empty")
    if preferred_backend == "remote":
        return _remote_request("/preview/start", payload={"image":image,"workspace_rel":_data_relative(workspace),"scratch_rel":_data_relative(scratch),"argv":argv,"name":name,"timeout_seconds":20,"cpu_limit":cpu_limit,"memory_mb":memory_mb,"process_limit":process_limit,"network_policy":network_policy,"env":{}}, timeout=30)
    info=detect_container_backend(preferred_backend); command=_base_run_args(info,image=image,workspace=workspace,scratch=scratch,cpu_limit=cpu_limit,memory_mb=memory_mb,process_limit=process_limit,network_policy=network_policy); command.insert(2,"-d"); command[3:3]=["--name",name]; command+=argv
    completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20,shell=False)
    if completed.returncode!=0: raise SandboxError((completed.stderr or "Failed to start preview container")[-4000:])
    return {"container_ref":completed.stdout.strip() or name,"backend":info.backend,"effective_network_policy":"deny"}


def exec_in_container(*, preferred_backend: str, container_ref: str, argv: list[str], timeout_seconds: int) -> dict:
    if preferred_backend == "remote":
        return _remote_request("/preview/exec",payload={"container_ref":container_ref,"argv":argv,"timeout_seconds":max(1,int(timeout_seconds))},timeout=max(15.0,float(timeout_seconds)+10))
    info=detect_container_backend(preferred_backend)
    if not info.available or not info.executable: raise SandboxError("Container sandbox runtime is unavailable")
    completed=subprocess.run([info.executable,"exec",container_ref,*argv],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=max(1,int(timeout_seconds)),shell=False)
    return {"argv":argv,"exit_code":completed.returncode,"stdout":completed.stdout[-10000:],"stderr":completed.stderr[-10000:]}


def stop_container(*, preferred_backend: str, container_ref: str) -> None:
    if not container_ref: return
    if preferred_backend == "remote":
        try: _remote_request("/preview/stop",payload={"container_ref":container_ref},timeout=12)
        except SandboxError: pass
        return
    info=detect_container_backend(preferred_backend)
    if not info.available or not info.executable: return
    subprocess.run([info.executable,"stop","--time","2",container_ref],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,shell=False)