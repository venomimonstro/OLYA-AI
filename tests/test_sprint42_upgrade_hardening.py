from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_32gb_upgrade_repairs_old_sandbox_and_runtime_memory_envelope():
    installer = (ROOT / "scripts/install.sh").read_text("utf-8")
    for marker in (
        "target_llama_memory_gb = llama_min_gib if safe_context <= 8192 else llama_memory_gb",
        "if safe_context <= 8192:",
        "X1_SANDBOX_MAX_MEMORY_MB",
        "X1_PROJECT_RUNTIME_MAX_MEMORY_MB",
        "X1_PROJECT_RUNTIME_DEFAULT_MEMORY_MB",
        "bounded_int('X1_SANDBOX_MAX_MEMORY_MB',1024,256,1024)",
    ):
        assert marker in installer


def test_larger_hosts_are_not_forced_into_32gb_sandbox_profile():
    installer = (ROOT / "scripts/install.sh").read_text("utf-8")
    block = installer.split("# Repair pre-Sprint42 installations", 1)[1].split("if new_env:", 1)[0]
    assert "if safe_context <= 8192:" in block
    assert "safe_context <= 12288" not in block
