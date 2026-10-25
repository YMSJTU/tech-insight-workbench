import time
from sqlalchemy import select, update, or_
from .db import Session, Job, WorkflowStep, uid, asdict

TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


class Cancelled(Exception):
    pass


class BudgetExceeded(Exception):
    pass


def submit(kind, payload, parent_id=None, resume_from=None):
    with Session.begin() as s:
        j = Job(
            kind=kind,
            payload=payload,
            parent_id=parent_id,
            max_calls=payload.get("max_calls", 16),
        )
        s.add(j)
        s.flush()
        if resume_from and kind == "report":
            for step in s.scalars(
                select(WorkflowStep).where(WorkflowStep.job_id == resume_from)
            ).all():
                s.add(
                    WorkflowStep(
                        job_id=j.id,
                        name=step.name,
                        input_hash=step.input_hash,
                        version=step.version,
                        output=step.output,
                    )
                )
        return asdict(j)


def claim():
    now = time.time()
    with Session.begin() as s:
        q = (
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    (Job.status == "running") & (Job.lease_until < now),
                )
            )
            .order_by(Job.created_at)
        )
        for j in s.scalars(q).all():
            token = uid()
            changed = s.execute(
                update(Job)
                .where(
                    Job.id == j.id,
                    Job.status == j.status,
                    Job.lease_until == j.lease_until,
                )
                .values(
                    status="running",
                    lease_token=token,
                    lease_until=now + 60,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
            )
            if changed.rowcount:
                return j.id, token
    return None


def check(s, id_, token):
    # Conditional update serializes cancellation/lease fencing with commits.
    r = s.execute(
        update(Job)
        .where(
            Job.id == id_,
            Job.lease_token == token,
            Job.status == "running",
            Job.cancel_requested == False,
            Job.lease_until > time.time(),
        )
        .values(updated_at=time.time())
    )
    if not r.rowcount:
        raise Cancelled("任务已取消或租约失效")


def stage(id_, token, name):
    with Session.begin() as s:
        check(s, id_, token)
        s.execute(update(Job).where(Job.id == id_).values(stage=name))


def finish(id_, token, status, result=None, error=""):
    with Session.begin() as s:
        j = s.get(Job, id_)
        if j.lease_token != token:
            return
        if j.cancel_requested:
            status = "cancelled"
        s.execute(
            update(Job)
            .where(Job.id == id_, Job.lease_token == token)
            .values(
                status=status,
                result=result or j.result,
                error=error,
                lease_until=0,
                stage={
                    "succeeded": "完成",
                    "partial": "部分完成",
                    "failed": "失败",
                    "cancelled": "已取消",
                    "waiting": "等待论文归档",
                }.get(status, status),
                updated_at=time.time(),
            )
        )


def is_cancelled(job_id):
    with Session() as s:
        j = s.get(Job, job_id)
        return bool(j and j.cancel_requested)


def remaining_calls(job_id):
    with Session() as s:
        j = s.get(Job, job_id)
        root = s.get(Job, j.parent_id or j.id) if j else None
        return max(0, (root.max_calls - root.calls_used) if root else 0)


def reserve_call(job_id):
    with Session.begin() as s:
        j = s.get(Job, job_id)
        if j.cancel_requested:
            raise Cancelled("任务已取消")
        root = j.parent_id or j.id
        r = s.execute(
            update(Job)
            .where(
                Job.id == root,
                Job.calls_used < Job.max_calls,
                Job.cancel_requested == False,
            )
            .values(calls_used=Job.calls_used + 1)
        )
        if not r.rowcount:
            raise BudgetExceeded("模型调用次数达到上限或父任务已取消")
        if root != j.id:
            s.execute(
                update(Job).where(Job.id == j.id).values(calls_used=Job.calls_used + 1)
            )


def add_tokens(job_id, n):
    with Session.begin() as s:
        j = s.get(Job, job_id)
        for id_ in {job_id, j.parent_id or job_id}:
            s.execute(
                update(Job)
                .where(Job.id == id_)
                .values(tokens_used=Job.tokens_used + max(0, int(n)))
            )


def aggregate():
    with Session.begin() as s:
        for j in s.scalars(select(Job).where(Job.status == "waiting")).all():
            children = s.scalars(select(Job).where(Job.parent_id == j.id)).all()
            if any(c.status not in TERMINAL for c in children):
                continue
            counts = {k: sum(c.status == k for c in children) for k in TERMINAL}
            result = {**j.result, "counts": counts}
            status = (
                "cancelled"
                if j.cancel_requested
                else (
                    "partial"
                    if result.get("warnings")
                    or any(c.status != "succeeded" for c in children)
                    else "succeeded"
                )
            )
            j.status = status
            j.result = result
            j.stage = "搜索与归档完成"
            j.updated_at = time.time()
