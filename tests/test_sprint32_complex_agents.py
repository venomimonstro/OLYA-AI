from app.db import Base, build_engine
from app.models import Project, User
from app.schemas.development import DevelopmentPlanCreate
from app.services.development import create_plan, serialize_plan


def test_complex_store_plan_can_be_partitioned_without_monolithic_agent_context():
    engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        user = User(email="shop@example.com", password_hash="x", display_name="Shop")
        db.add(user); db.flush()
        project = Project(owner_id=user.id, name="Complex Store", instructions="")
        db.add(project); db.flush()
        payload = DevelopmentPlanCreate.model_validate({
            "project_id": project.id,
            "title": "Complex ecommerce",
            "product_brief": "Catalog, search, cart, checkout, orders, admin, integrations and security",
            "requirements": [{"key": f"r{i}", "text": f"requirement {i}", "priority": "must"} for i in range(12)],
            "architecture": {"style": "modular"},
            "constraints": ["low resource server"],
            "sprints": [
                {"ordinal": i, "title": f"Sprint {i}", "goal": f"deliver slice {i}", "dependencies": ([] if i == 1 else [i-1]),
                 "acceptance_criteria": [f"criterion {i}"],
                 "items": [{"ordinal": 1, "title": f"module {i}", "goal": f"implement module {i}", "kind": "feature", "dependencies": [], "acceptance_criteria": [f"criterion {i}"]}]}
                for i in range(1, 9)
            ],
        })
        plan = create_plan(db, user_id=user.id, payload=payload)
        data = serialize_plan(db, plan)
        assert len(data["sprints"]) == 8
        assert all(len(s["items"]) == 1 for s in data["sprints"])
        assert data["sprints"][7]["dependencies"] == [7]
