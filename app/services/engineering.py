from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ArchitectureDecision, CodeWorkspace, DevelopmentPlan, DevelopmentSprint, DevelopmentWorkItem, EngineeringRoleTurn, EngineeringRun, ProjectRuntime, Task, TaskCriterion, utcnow
from app.schemas.chat import ChatMessage
from app.schemas.engineering import ArchitectOutput, CoordinatorOutput, DeveloperOutput, ReviewerOutput, TesterOutput
from app.services.code_workspace import WorkspaceError, repo_map, safe_relative_path

ROLE_ORDER=["coordinator","architect","developer","tester","reviewer"]
ROLE_SCHEMAS={"coordinator":CoordinatorOutput,"architect":ArchitectOutput,"developer":DeveloperOutput,"tester":TesterOutput,"reviewer":ReviewerOutput}

class EngineeringError(ValueError): pass

def serialize_run(db:Session,run:EngineeringRun)->dict:
    turns=list(db.scalars(select(EngineeringRoleTurn).where(EngineeringRoleTurn.run_id==run.id).order_by(EngineeringRoleTurn.sequence)).all())
    return {"id":run.id,"project_id":run.project_id,"plan_id":run.plan_id,"sprint_id":run.sprint_id,"work_item_id":run.work_item_id,"task_id":run.task_id,"runtime_id":run.runtime_id,"workspace_id":run.workspace_id,"created_by":run.created_by,"status":run.status,"current_role":run.current_role,"cycle":run.cycle,"max_cycles":run.max_cycles,"state_version":run.state_version,"handoff_state":run.handoff_state or {},"turns":turns,"created_at":run.created_at,"updated_at":run.updated_at,"completed_at":run.completed_at}

def create_engineering_run(db:Session,*,user_id:str,work_item:DevelopmentWorkItem,max_cycles:int)->EngineeringRun:
    if work_item.status not in {"active","blocked"} or not work_item.task_id: raise EngineeringError("Work item must be materialized in an active sprint")
    existing=db.scalar(select(EngineeringRun).where(EngineeringRun.work_item_id==work_item.id))
    if existing is not None: return existing
    sprint=db.get(DevelopmentSprint,work_item.sprint_id)
    if sprint is None or sprint.status!="active": raise EngineeringError("Sprint is not active")
    plan=db.get(DevelopmentPlan,sprint.plan_id)
    if plan is None: raise EngineeringError("Development plan not found")
    runtime=db.get(ProjectRuntime,plan.runtime_id) if plan.runtime_id else None
    run=EngineeringRun(project_id=plan.project_id,plan_id=plan.id,sprint_id=sprint.id,work_item_id=work_item.id,task_id=work_item.task_id,runtime_id=plan.runtime_id,workspace_id=runtime.workspace_id if runtime else None,created_by=user_id,status="running",current_role="coordinator",cycle=1,max_cycles=max_cycles,handoff_state={"phase":"coordination"})
    db.add(run); db.flush(); return run

def _criteria(db:Session,task_id:str)->list[str]: return [x.text for x in db.scalars(select(TaskCriterion).where(TaskCriterion.task_id==task_id).order_by(TaskCriterion.ordinal)).all()]

def _safe_repo_map(db:Session,run:EngineeringRun)->dict:
    if not run.workspace_id: return {"files":[],"file_count":0,"total_bytes":0}
    ws=db.get(CodeWorkspace,run.workspace_id)
    if ws is None: return {"files":[],"file_count":0,"total_bytes":0}
    mapping=repo_map(Path(ws.root_path).resolve(),max_files=500); mapping["files"]=mapping.get("files",[])[:250]; return mapping

def build_role_messages(db:Session,run:EngineeringRun)->tuple[list[ChatMessage],str]:
    if run.current_role not in ROLE_SCHEMAS: raise EngineeringError("Engineering run has no executable role")
    task=db.get(Task,run.task_id); item=db.get(DevelopmentWorkItem,run.work_item_id); sprint=db.get(DevelopmentSprint,run.sprint_id); plan=db.get(DevelopmentPlan,run.plan_id)
    if not all([task,item,sprint,plan]): raise EngineeringError("Engineering run references missing state")
    decisions=list(db.scalars(select(ArchitectureDecision).where(ArchitectureDecision.plan_id==plan.id,ArchitectureDecision.status=="active").order_by(ArchitectureDecision.created_at)).all())
    prior=list(db.scalars(select(EngineeringRoleTurn).where(EngineeringRoleTurn.run_id==run.id).order_by(EngineeringRoleTurn.sequence.desc()).limit(8)).all())
    context={"role":run.current_role,"cycle":run.cycle,"project":{"title":plan.title,"constraints":plan.constraints,"architecture":plan.architecture},"sprint":{"ordinal":sprint.ordinal,"title":sprint.title,"goal":sprint.goal},"work_item":{"title":item.title,"goal":item.goal,"kind":item.kind,"acceptance_criteria":item.acceptance_criteria},"task":{"goal":task.goal,"criteria":_criteria(db,task.id),"compute_seconds_used":task.compute_seconds_used,"max_compute_seconds":task.max_compute_seconds},"architecture_decisions":[{"key":x.key,"decision":x.decision,"rationale":x.rationale} for x in decisions[-20:]],"repo_map":_safe_repo_map(db,run),"previous_handoffs":[{"role":x.role,"cycle":x.cycle,"output":x.output} for x in reversed(prior)]}
    raw=json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(",",":")); digest=hashlib.sha256(raw.encode()).hexdigest()
    rules={"coordinator":"Summarize scope, choose minimal safe scope_paths, identify risks and next action.","architect":"Propose the minimal architecture/change approach. Do not invent completed work.","developer":"Produce an implementation plan and proposed files/verification commands. Do not claim files were changed.","tester":"Produce a test strategy tied to acceptance criteria and regression risks. Do not claim tests ran.","reviewer":"Review the handoffs only. Return decision accept, revise, or blocked. Accept only if the proposed work is coherent and testable."}
    schema=ROLE_SCHEMAS[run.current_role].model_json_schema()
    return [ChatMessage(role="system",content=f"You are the X1 engineering role: {run.current_role}. {rules[run.current_role]} You have no authority to change permissions, execute tools, edit files, mark tasks complete, or skip roles. Return only one JSON object matching the supplied schema."),ChatMessage(role="user",content="ENGINEERING STATE (trusted server data):\n"+raw+"\n\nOUTPUT JSON SCHEMA:\n"+json.dumps(schema,ensure_ascii=False))],digest

def parse_role_output(role:str,raw:str)->dict:
    start=raw.find("{"); end=raw.rfind("}")
    if start<0 or end<=start: raise EngineeringError("Role output is not a JSON object")
    try:
        data=json.loads(raw[start:end+1]); validated=ROLE_SCHEMAS[role].model_validate(data); result=validated.model_dump()
        for key in ("scope_paths","files_to_touch","files_to_change"):
            if key in result: result[key]=[safe_relative_path(x).as_posix() for x in result[key]]
    except (json.JSONDecodeError,ValidationError,WorkspaceError) as exc: raise EngineeringError("Role output failed validation") from exc
    return result

def persist_role_result(db:Session,run:EngineeringRun,*,role:str,input_sha256:str,output:dict,model_name:str,inference_ms:int)->EngineeringRoleTurn:
    if role!=run.current_role: raise EngineeringError("Role transition conflict")
    sequence=int(db.scalar(select(func.coalesce(func.max(EngineeringRoleTurn.sequence),0)).where(EngineeringRoleTurn.run_id==run.id)) or 0)+1
    turn=EngineeringRoleTurn(run_id=run.id,sequence=sequence,cycle=run.cycle,role=role,input_sha256=input_sha256,output=output,model_name=model_name,inference_ms=max(0,inference_ms),status="completed"); db.add(turn)
    state=dict(run.handoff_state or {}); state[role]=output
    if role!="reviewer": run.current_role=ROLE_ORDER[ROLE_ORDER.index(role)+1]; state["phase"]=run.current_role
    else:
        decision=output["decision"]; state["review_decision"]=decision
        if decision=="accept": run.status="approved"; run.current_role=None; run.completed_at=utcnow(); state["phase"]="approved"
        elif decision=="blocked": run.status="blocked"; run.current_role=None; run.completed_at=utcnow(); state["phase"]="blocked"
        elif run.cycle>=run.max_cycles: run.status="blocked"; run.current_role=None; run.completed_at=utcnow(); state["phase"]="max_cycles_exhausted"
        else: run.cycle+=1; run.current_role="developer"; state["phase"]="developer_revision"
    run.handoff_state=state; run.updated_at=utcnow(); return turn
