"""Lease-fenced persistence of completed workflow steps."""

from sqlalchemy import select
from .db import Session, WorkflowStep
from .tasks import check


class StepRepository:
    def load(self, job_id, token, name, input_hash):
        with Session.begin() as s:
            check(s, job_id, token)
            step = s.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.job_id == job_id,
                    WorkflowStep.name == name,
                    WorkflowStep.input_hash == input_hash,
                )
            )
            return step.output if step else None

    def save(self, job_id, token, name, input_hash, version, output):
        with Session.begin() as s:
            check(s, job_id, token)
            step = s.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.job_id == job_id,
                    WorkflowStep.name == name,
                    WorkflowStep.input_hash == input_hash,
                )
            )
            if step is None:
                s.add(
                    WorkflowStep(
                        job_id=job_id,
                        name=name,
                        input_hash=input_hash,
                        version=version,
                        output=output,
                    )
                )
