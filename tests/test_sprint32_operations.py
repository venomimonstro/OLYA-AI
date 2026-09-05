from app.db import Base, build_engine
from app.models import UsageEvent, User
from app.models_sprint27 import Organization
from app.models_sprint29 import BetaParticipant
from app.models_sprint31 import SystemCheckpoint
from app.services.operations_analytics import operations_summary


def test_sprint_models_register_together_and_ops_summary_is_bounded():
    engine = build_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        user = User(email="ops@example.com", password_hash="x", display_name="Ops")
        db.add(user); db.flush()
        db.add(UsageEvent(user_id=user.id, mode="fast", raw_chars=1000, compiled_chars=400, output_chars=100, duration_ms=1200, inference_ms=900, queue_ms=20, success=True))
        db.flush()
        summary = operations_summary(db, window_hours=24, monthly_server_cost_rub=4000)
        assert summary["traffic"]["requests"] == 1
        assert summary["traffic"]["success_rate"] == 1.0
        assert summary["traffic"]["context_efficiency_ratio"] == 0.4
        assert summary["economics"]["allocated_server_cost_rub"] > 0
        assert "images" in summary and "agents" in summary
