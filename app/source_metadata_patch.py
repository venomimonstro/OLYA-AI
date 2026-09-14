from __future__ import annotations


def install_source_metadata_patch() -> None:
    """Expose only evidence that actually grounded the answer.

    Fetched snapshots remain the strongest evidence. For trivial current-role
    lookups, an explicitly confirmed live search row from the canonical official
    domain may also ground the deterministic answer when that site blocks HTML
    fetching. Ordinary search-only candidates stay hidden.
    """
    from app.services.task_solver import TaskExecution

    current = TaskExecution.public_metadata
    if getattr(current, "_olya_verified_source_surface", False):
        return

    def public_metadata(self) -> dict:
        payload = current(self)
        rows = list(payload.get("sources") or [])
        if any(isinstance(row, dict) and ("verified" in row or "search_confirmed" in row) for row in rows):
            grounded = [
                row for row in rows
                if isinstance(row, dict) and (row.get("verified") is True or row.get("search_confirmed") is True)
            ]
            payload["sources"] = grounded
            payload["verified_sources"] = sum(1 for row in grounded if row.get("verified") is True)
            payload["search_confirmed_sources"] = sum(1 for row in grounded if row.get("search_confirmed") is True)
            payload["search_candidates"] = len(rows)
        return payload

    public_metadata._olya_verified_source_surface = True  # type: ignore[attr-defined]
    TaskExecution.public_metadata = public_metadata
