# Sprint 42 — Qwen3.6 migration / implementation status

## Статус

**Implementation complete in source; target-node acceptance required before public production claim.**

Целевая production-модель: `Qwen3.6-35B-A3B-Q4_K_M`.

## Реализовано

- единый `model-manifest.json` с repository/revision/file/SHA-256/exact size;
- immutable Qwen3.6 artifact и отдельный legacy rollback profile;
- downloader с resumable download, exact-size и SHA-256 verification;
- 32-GiB host policy: 8K context, один generation slot, минимум 8 GiB вне llama;
- 48-GiB и 64-GiB context ceilings описаны manifest policy;
- скрытый automatic downgrade Q4_K_M → Q4_K_S/IQ4_XS запрещён;
- installer нормализует model identity, RAM и context старых установок;
- Compose использует новый model file и pinned llama.cpp runtime;
- Doctor проверяет model identity, size, host-memory и context envelope;
- transactional updater сохраняет и восстанавливает прежнюю `.env` при rollback;
- предыдущий GGUF не удаляется миграцией;
- historical regression contracts переведены на новую модель;
- отдельные Sprint 42 tests проверяют manifest, host tiers, installer, downloader, Compose, Doctor и rollback;
- `git_collaboration.py` восстановлен в обычный Python вместо opaque `exec` wrapper, чтобы обязательный static security audit снова мог анализировать код;
- Git service дополнительно отключает hooks и опасные `file`/`ext` transports, redacts credentials и выполняет secret scan перед push.

## Что нельзя считать выполненным только по исходному коду

Перед публичным релизом на реальном 32-GiB target node обязательны:

1. фактическая загрузка и SHA-256 проверка GGUF;
2. cold start llama.cpp;
3. 8K long-context probe;
4. RAM/RSS/swap calibration;
5. latency/TTFT/tokens-per-second measurement;
6. полный `release_gate.py --runtime --live-inference --user-journey --chaos`;
7. backup/restore drill;
8. сравнение пользовательского regression corpus с last-known-good baseline;
9. controlled rollback drill на предыдущий revision.

До прохождения этих пунктов корректный статус — **implementation complete, production target not yet empirically certified**.
