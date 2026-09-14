from __future__ import annotations


def install_source_metadata_patch() -> None:
    """Keep primary answer citations aligned with evidence actually fetched.

    Fast web grounding may retain search-only candidates internally for discovery
    diagnostics. They must not be presented to the user as sources that grounded
    the final answer unless the page itself was successfully fetched and became a
    ResearchSource snapshot. Full/deep solver sources predate the `verified`
    marker and are already fetched snapshots, so they are preserved unchanged.
    """
    from app.services.task_solver import TaskExecution

    current = TaskExecution.public_metadata
    if getattr(current, "_olya_verified_source_surface", False):
        return

    def public_metadata(self) -> dict:
        payload = current(self)
        rows = list(payload.get("sources") or [])
        if any(isinstance(row, dict) and "verified" in row for row in rows):
            verified = [row for row in rows if isinstance(row, dict) and row.get("verified") is True]
            payload["sources"] = verified
            payload["verified_sources"] = len(verified)
            payload["search_candidates"] = len(rows)
        return payload

    public_metadata._olya_verified_source_surface = True  # type: ignore[attr-defined]
    TaskExecution.public_metadata = public_metadata
