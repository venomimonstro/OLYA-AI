from __future__ import annotations


def install_task_solver_access_patch() -> None:
    """Guard all server-side web solvers before they persist project-scoped data."""
    from app.services import fast_web_grounding, task_solver
    from app.services.access import require_project_role

    current_deep = task_solver.execute_task_solver
    if not getattr(current_deep, "_olya_project_access_guard", False):
        async def guarded_deep(*, db, user, settings, discovery, fetcher, question, project_id, force_web=False):
            if project_id:
                require_project_role(db, user, project_id, "member")
            return await current_deep(
                db=db,
                user=user,
                settings=settings,
                discovery=discovery,
                fetcher=fetcher,
                question=question,
                project_id=project_id,
                force_web=force_web,
            )

        guarded_deep._olya_project_access_guard = True  # type: ignore[attr-defined]
        task_solver.execute_task_solver = guarded_deep

    current_fast = fast_web_grounding.execute_fast_web_grounding
    if not getattr(current_fast, "_olya_project_access_guard", False):
        async def guarded_fast(*, db, user, settings, discovery, fetcher, question, project_id, force_web=False):
            if project_id:
                require_project_role(db, user, project_id, "member")
            return await current_fast(
                db=db,
                user=user,
                settings=settings,
                discovery=discovery,
                fetcher=fetcher,
                question=question,
                project_id=project_id,
                force_web=force_web,
            )

        guarded_fast._olya_project_access_guard = True  # type: ignore[attr-defined]
        fast_web_grounding.execute_fast_web_grounding = guarded_fast
