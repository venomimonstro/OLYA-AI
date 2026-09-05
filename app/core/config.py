from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config=SettingsConfigDict(env_file=".env",env_prefix="X1_",extra="ignore")
    env:str="development"; host:str="0.0.0.0"; port:int=8000
    database_url:str="sqlite+pysqlite:///./x1.db"; llama_base_url:str="http://127.0.0.1:8080"; llama_model_name:str="Qwen3.6-35B-A3B-Q4_K_M"
    max_context_tokens:int=8192; deep_context_tokens:int=16384; max_concurrent_generations:int=1; max_queue_size:int=64; default_max_output_tokens:int=1200; request_timeout_seconds:int=180
    admin_bootstrap_token:str="change-me"; session_ttl_days:int=30
    file_storage_path:str="./data/files"; max_file_size_bytes:int=20*1024*1024; file_chunk_chars:int=1600; file_chunk_overlap_chars:int=180; file_context_chunks:int=6; max_pdf_pages:int=500; max_docx_unpacked_bytes:int=100*1024*1024
    document_storage_path:str="./data/documents"; document_render_timeout_seconds:int=60; document_max_pages:int=300
    database_auto_create_schema:bool=False; default_monthly_compute_seconds:int=600; default_max_concurrent_inference:int=1; default_max_concurrent_jobs:int=1; job_lease_seconds:int=120; job_poll_seconds:float=1.0
    research_timeout_seconds:float=15.0; research_max_bytes:int=2_000_000; research_max_chars:int=500_000; research_max_redirects:int=3
    search_provider:str="disabled"; search_providers:str=""; search_cache_ttl_seconds:int=3600; brave_search_api_key:str=""; search_timeout_seconds:float=10.0; research_max_search_queries:int=6; research_max_discovery_results:int=30
    frustration_slow_queue_ms:int=5000; frustration_slow_response_ms:int=120000
    code_workspace_storage_path:str="./data/code_workspaces"; code_workspace_max_archive_bytes:int=25*1024*1024; code_workspace_max_unpacked_bytes:int=100*1024*1024; code_workspace_max_files:int=5000; code_allow_unsafe_commands:bool=False
    project_runtime_storage_path:str="./data/project_runtimes"; project_runtime_default_cpu_limit:float=1.0; project_runtime_default_memory_mb:int=1024; project_runtime_default_disk_mb:int=2048; project_runtime_default_process_limit:int=64; project_runtime_secret_key:str="change-me-runtime-secret"
    project_sandbox_backend:str="auto"; project_sandbox_image:str=""; project_sandbox_command_timeout_seconds:int=300; project_sandbox_preview_timeout_seconds:int=120
    image_storage_path:str="./data/images"; image_backend:str="disabled"; image_model_name:str=""; image_model_path:str=""; image_max_dimension:int=1536; image_max_pixels:int=1536*1536; image_max_steps:int=50; image_default_steps:int=24; image_max_active_per_user:int=1; image_job_priority:int=150; image_worker_idle_exit_seconds:int=30
    image_qa_max_repairs:int=1; image_perceptual_error_max:float=4.0; image_preview_max_side:int=512; image_storage_min_free_bytes:int=2*1024*1024*1024; image_storage_min_free_percent:float=10.0; image_user_storage_quota_bytes:int=2*1024*1024*1024; image_rejected_retention_days:int=7; image_vision_qa_url:str=""; image_vision_qa_timeout_seconds:int=45
    monthly_server_cost_rub:float=4000.0; chat_history_messages:int=48; chat_message_page_size:int=100
    commerce_cpu_microunits_per_second:int=1000; commerce_gpu_microunits_per_second:int=10000; commerce_image_worker_microunits_per_second:int=5000; commerce_sandbox_microunits_per_second:int=2000
    payment_ingest_secret:str=""; api_default_rate_limit_per_minute:int=60; api_max_rate_limit_per_minute:int=600
    backup_storage_path:str="./backups"; health_checkpoint_stale_seconds:int=300; health_backup_max_age_hours:float=36.0

@lru_cache
def get_settings()->Settings: return Settings()
