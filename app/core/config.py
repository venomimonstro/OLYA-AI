from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="X1_", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    data_root: str = "./data"
    database_url: str = "sqlite+pysqlite:///./data/x1.db"
    database_pool_size: int = 8
    database_max_overflow: int = 4
    database_pool_timeout_seconds: float = 5.0
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 5
    database_statement_timeout_ms: int = 30000
    database_lock_timeout_ms: int = 10000
    database_idle_transaction_timeout_ms: int = 60000

    llama_base_url: str = "http://127.0.0.1:8080"
    llama_model_name: str = "Qwen3.6-35B-A3B-Q4_K_M"
    max_context_tokens: int = 8192
    deep_context_tokens: int = 8192
    max_concurrent_generations: int = 1
    max_queue_size: int = 64
    inference_queue_timeout_seconds: float = 120.0
    default_max_output_tokens: int = 1200
    request_timeout_seconds: int = 180

    # Sprint 54 pre-DB overload lanes. These protect DB/session allocation while
    # preserving the deeper inference/job governors as separate safety layers.
    overload_chat_max_active_http: int = 4
    overload_chat_max_queue: int = 32
    overload_chat_queue_timeout_seconds: float = 90.0
    overload_research_max_active_http: int = 4
    overload_research_max_queue: int = 24
    overload_research_queue_timeout_seconds: float = 15.0
    overload_image_max_active_http: int = 2
    overload_image_max_queue: int = 8
    overload_image_queue_timeout_seconds: float = 10.0
    overload_sandbox_max_active_http: int = 2
    overload_sandbox_max_queue: int = 8
    overload_sandbox_queue_timeout_seconds: float = 15.0
    overload_max_queued_per_principal: int = 2
    overload_breaker_failures: int = 5
    overload_breaker_cooldown_seconds: float = 20.0

    admin_bootstrap_token: str = "change-me"
    session_ttl_days: int = 30
    session_max_active_per_user: int = 20

    file_storage_path: str = "./data/files"
    max_file_size_bytes: int = 20 * 1024 * 1024
    file_chunk_chars: int = 1600
    file_chunk_overlap_chars: int = 180
    file_context_chunks: int = 6
    max_pdf_pages: int = 500
    max_docx_unpacked_bytes: int = 100 * 1024 * 1024
    file_parse_timeout_seconds: int = 45
    file_parse_memory_mb: int = 768
    file_parse_queue_timeout_seconds: float = 5.0
    file_max_extracted_chars: int = 2_000_000
    file_user_storage_quota_bytes: int = 2 * 1024 * 1024 * 1024
    file_storage_min_free_bytes: int = 2 * 1024 * 1024 * 1024
    file_storage_min_free_percent: float = 10.0

    document_storage_path: str = "./data/documents"
    document_render_timeout_seconds: int = 60
    document_max_pages: int = 300
    document_max_concurrent_renders: int = 1
    document_render_queue_timeout_seconds: float = 5.0
    document_render_backend: str = "local"
    document_render_worker_url: str = "http://document-worker:8091"
    document_render_worker_token: str = "change-me-document-worker"
    document_raster_dpi: int = 110
    document_qa_max_repairs: int = 1

    database_auto_create_schema: bool = False
    default_monthly_compute_seconds: int = 600
    default_max_concurrent_inference: int = 1
    default_max_concurrent_jobs: int = 1
    job_lease_seconds: int = 120
    job_poll_seconds: float = 1.0

    research_timeout_seconds: float = 15.0
    research_max_bytes: int = 2_000_000
    research_max_chars: int = 500_000
    research_max_redirects: int = 3
    research_max_concurrent_operations: int = 4
    research_max_queue_size: int = 32
    research_queue_timeout_seconds: float = 15.0
    research_freshness_max_age_seconds: int = 15 * 60
    research_freshness_min_independent_hosts: int = 2
    search_provider: str = "searxng"
    search_providers: str = "searxng"
    searxng_base_url: str = "http://searxng:8080"
    search_cache_ttl_seconds: int = 3600
    brave_search_api_key: str = ""
    search_timeout_seconds: float = 10.0
    research_max_search_queries: int = 6
    research_max_discovery_results: int = 30

    frustration_slow_queue_ms: int = 5000
    frustration_slow_response_ms: int = 120000

    code_workspace_storage_path: str = "./data/code_workspaces"
    code_workspace_max_archive_bytes: int = 25 * 1024 * 1024
    code_workspace_max_unpacked_bytes: int = 100 * 1024 * 1024
    code_workspace_max_files: int = 5000
    code_allow_unsafe_commands: bool = False

    project_runtime_storage_path: str = "./data/project_runtimes"
    project_runtime_default_cpu_limit: float = 1.0
    project_runtime_default_memory_mb: int = 1024
    project_runtime_default_disk_mb: int = 2048
    project_runtime_default_process_limit: int = 64
    project_runtime_max_cpu_limit: float = 1.0
    project_runtime_max_memory_mb: int = 2048
    project_runtime_max_process_limit: int = 128
    project_runtime_secret_key: str = "change-me-runtime-secret"
    project_sandbox_backend: str = "remote"
    project_sandbox_image: str = "x1-sandbox:0.39"
    project_sandbox_worker_url: str = "http://sandbox-worker:8090"
    project_sandbox_worker_token: str = "change-me-sandbox-worker"
    project_sandbox_command_timeout_seconds: int = 300
    project_sandbox_preview_timeout_seconds: int = 120
    sandbox_max_concurrent_executions: int = 1
    sandbox_max_active_previews: int = 1
    sandbox_max_memory_mb: int = 2048
    sandbox_max_cpu: float = 1.0
    sandbox_max_pids: int = 128
    sandbox_preview_ttl_seconds: int = 900

    image_storage_path: str = "./data/images"
    image_backend: str = "disabled"
    image_model_name: str = ""
    image_model_path: str = ""
    image_max_dimension: int = 1536
    image_max_pixels: int = 1536 * 1536
    image_max_steps: int = 50
    image_default_steps: int = 24
    image_max_active_per_user: int = 1
    image_job_priority: int = 150
    image_worker_idle_exit_seconds: int = 30
    image_qa_max_repairs: int = 1
    image_perceptual_error_max: float = 4.0
    image_preview_max_side: int = 512
    image_storage_min_free_bytes: int = 2 * 1024 * 1024 * 1024
    image_storage_min_free_percent: float = 10.0
    image_user_storage_quota_bytes: int = 2 * 1024 * 1024 * 1024
    image_rejected_retention_days: int = 7
    image_vision_qa_url: str = ""
    image_vision_qa_timeout_seconds: int = 45

    monthly_server_cost_rub: float = 4000.0
    chat_history_messages: int = 48
    chat_message_page_size: int = 100

    commerce_cpu_microunits_per_second: int = 1000
    commerce_gpu_microunits_per_second: int = 10000
    commerce_image_worker_microunits_per_second: int = 5000
    commerce_sandbox_microunits_per_second: int = 2000
    payment_ingest_secret: str = ""
    api_default_rate_limit_per_minute: int = 60
    api_max_rate_limit_per_minute: int = 600

    backup_storage_path: str = "./backups"
    health_checkpoint_stale_seconds: int = 300
    health_backup_max_age_hours: float = 36.0
    release_gate_report_path: str = "./backups/release-gate-latest.json"
    restore_drill_report_path: str = "./backups/restore-drill-latest.json"
    release_gate_max_age_hours: float = 24.0
    restore_drill_max_age_hours: float = 168.0

    maintenance_enabled: bool = True
    maintenance_interval_seconds: float = 3600.0
    api_rate_window_retention_hours: int = 2
    search_cache_retention_hours: int = 24
    expired_session_retention_days: int = 7
    system_health_snapshot_retention_days: int = 30
    background_job_success_retention_days: int = 30
    background_job_failure_retention_days: int = 90

    capacity_report_path: str = "./backups/capacity-latest.json"
    capacity_report_max_age_hours: float = 168.0
    capacity_compute_headroom_ratio: float = 1.25
    capacity_min_monthly_compute_seconds: int = 300
    capacity_max_monthly_compute_seconds: int = 14400
    beta_min_participants: int = 50
    beta_max_participants: int = 100
    beta_min_tasks: int = 500
    beta_min_request_success_rate: float = 0.97
    beta_max_frustration_per_request: float = 0.05
    beta_max_p95_queue_ms: int = 5000

    beta_wave_default_size: int = 10
    beta_wave_min_observation_requests: int = 50
    beta_wave_max_queue_regression_ratio: float = 1.50
    beta_wave_max_duration_regression_ratio: float = 1.50
    beta_wave_min_cpu_efficiency_ratio: float = 0.70
    beta_trend_max_success_drop: float = 0.02
    beta_trend_max_frustration_increase: float = 0.02
    beta_trend_max_quality_drop: float = 0.05
    beta_operations_scheduler_enabled: bool = True
    beta_operations_cohort: str = "closed-beta-1"
    beta_operations_window_days: int = 30
    beta_operations_check_interval_seconds: float = 3600.0
    beta_snapshot_interval_hours: float = 24.0

    public_launch_breaker_min_requests: int = 50
    public_launch_canary_min_requests: int = 30
    public_launch_max_failure_rate: float = 0.03
    public_launch_max_requests_per_user_hour: int = 120
    public_launch_global_budget_microunits: int = 0
    public_launch_enforce_exposure: bool = True
    public_launch_watchdog_enabled: bool = True
    public_launch_watchdog_interval_seconds: float = 300.0
    public_launch_auto_rollback: bool = True

    plan_ratio_free: float = 0.25
    plan_ratio_x1: float = 1.0
    plan_ratio_pro: float = 2.0
    plan_ratio_max: float = 4.0
    plan_ratio_business: float = 8.0
    plan_share_fast: float = 0.20
    plan_share_work: float = 0.35
    plan_share_deep: float = 0.25
    plan_share_api: float = 0.10
    plan_share_image: float = 0.05
    plan_share_sandbox: float = 0.05

    @property
    def is_sqlite(self) -> bool:
        return str(self.database_url).lower().startswith("sqlite")

    @property
    def database_host(self) -> str:
        try:
            return urlsplit(str(self.database_url).replace("postgresql+psycopg", "postgresql", 1)).hostname or ""
        except ValueError:
            return ""

    def ensure_storage_paths(self) -> None:
        for value in (
            self.data_root,
            self.file_storage_path,
            self.document_storage_path,
            self.code_workspace_storage_path,
            self.project_runtime_storage_path,
            self.image_storage_path,
            self.backup_storage_path,
        ):
            Path(value).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
