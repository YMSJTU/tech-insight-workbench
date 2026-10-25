"""Resumable execution independent of SQLAlchemy and ACP transport."""

import hashlib
import json
from .step_repository import StepRepository

REPORT_VERSION = "report-workflow-v2/prompt-v1"


class ReportExecution:
    def __init__(self, job_id, token, model, repository=None, version=REPORT_VERSION):
        self.job_id, self.token = job_id, token
        self.model, self.version = model, version
        self.repository = repository if repository is not None else StepRepository()

    def key(self, inputs):
        return hashlib.sha256(
            json.dumps(
                {"inputs": inputs, "model": self.model, "version": self.version},
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()

    def cached(self, name, inputs):
        return (
            self.repository.load(self.job_id, self.token, name, self.key(inputs))
            is not None
        )

    def run(self, name, inputs, operation):
        key = self.key(inputs)
        saved = self.repository.load(self.job_id, self.token, name, key)
        if saved is not None:
            return saved
        output = operation()
        self.repository.save(self.job_id, self.token, name, key, self.version, output)
        return output
