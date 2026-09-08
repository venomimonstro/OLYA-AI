from pathlib import Path

from app.services.server_profiles import (
    PROFILE_NAMES,
    profile_for_host,
    read_active_profile,
    read_staged_profile,
    persist_active_profile,
    stage_profile_request,
)


def test_profiles_have_expected_32gb_contexts():
    low = profile_for_host('super_low', 32.0, 8)
    optimal = profile_for_host('optimal', 32.0, 8)
    maximum = profile_for_host('maximum', 32.0, 8)
    assert low.context_tokens == 4096
    assert optimal.context_tokens == 8192
    assert maximum.context_tokens == 8192
    assert low.max_concurrent_generations == optimal.max_concurrent_generations == maximum.max_concurrent_generations == 1


def test_maximum_cannot_exceed_physical_context_ceiling():
    medium = profile_for_host('maximum', 48.0, 16)
    large = profile_for_host('maximum', 64.0, 24)
    assert medium.context_tokens == medium.safe_context_ceiling == 12288
    assert large.context_tokens == large.safe_context_ceiling == 16384
    assert medium.llama_memory_gib <= 24
    assert large.llama_memory_gib <= 24


def test_super_low_reduces_secondary_parallelism():
    low = profile_for_host('super_low', 32.0, 8)
    optimal = profile_for_host('optimal', 32.0, 8)
    assert low.research_concurrency < optimal.research_concurrency
    assert low.research_queue < optimal.research_queue
    assert low.sandbox_max_memory_mb < optimal.sandbox_max_memory_mb
    assert low.document_concurrency == 1
    assert low.images_default_enabled is False


def test_profile_env_is_one_coherent_boot_envelope():
    env = profile_for_host('optimal', 32.0, 12).env()
    for key in (
        'X1_SERVER_OPTIMIZATION_PROFILE',
        'X1_DEEP_CONTEXT_TOKENS',
        'X1_LLAMA_MEMORY_LIMIT',
        'X1_DATABASE_POOL_SIZE',
        'X1_APP_MEMORY_LIMIT_MB',
        'X1_DB_MEMORY_LIMIT_MB',
        'X1_DOCUMENT_MAX_CONCURRENT_RENDERS',
        'X1_RESEARCH_MAX_CONCURRENT_OPERATIONS',
        'X1_OVERLOAD_CHAT_MAX_QUEUE',
        'X1_SANDBOX_MAX_MEMORY_MB',
    ):
        assert key in env


def test_staged_and_active_profiles_are_distinct(tmp_path: Path):
    persist_active_profile(tmp_path, 'optimal', 32.0, 8)
    stage_profile_request(tmp_path, 'super_low', 32.0, 8)
    active = read_active_profile(tmp_path)
    staged = read_staged_profile(tmp_path)
    assert active['envelope']['profile'] == 'optimal'
    assert staged['envelope']['profile'] == 'super_low'
    assert active['path'] != staged['path']


def test_runtime_switch_is_staged_not_in_place():
    source = Path('app/api/routes/operations_analytics.py').read_text('utf-8')
    assert 'restart_required' in source
    assert 'active_operations_unchanged' in source
    assert 'stage_profile_request' in source
    assert 'ResourceGovernor(' not in source
    assert 'FairOverloadLane(' not in source


def test_compose_memory_limits_follow_profile_env():
    compose = Path('docker-compose.yml').read_text('utf-8')
    for marker in (
        '${X1_APP_MEMORY_LIMIT_MB:-2048}m',
        '${X1_DB_MEMORY_LIMIT_MB:-1536}m',
        '${X1_SEARX_MEMORY_LIMIT_MB:-768}m',
        '${X1_SANDBOX_WORKER_MEMORY_LIMIT_MB:-384}m',
        '${X1_DOCUMENT_WORKER_MEMORY_LIMIT_MB:-768}m',
        '"${X1_MAX_CONCURRENT_GENERATIONS:-1}"',
    ):
        assert marker in compose


def test_profile_applicator_is_atomic_and_persists_active_envelope():
    source = Path('scripts/apply_server_profile.py').read_text('utf-8')
    assert 'os.replace(tmp, path)' in source
    assert 'persist_active_profile' in source
    assert '--staged' in source


def test_all_public_profiles_are_supported():
    assert PROFILE_NAMES == ('super_low', 'optimal', 'maximum')
