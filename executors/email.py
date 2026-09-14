"""email_executor: sends one approved pitch. One credential: outreach-smtp (json: host, port, user,
password, from). Rate limited to the batch cap per run. Reversal: a short retraction to the same address."""
from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from typing import Any

from config.secrets import resolve_secret
from config.settings import get_settings
from db.connection import ProjectScope
from executors.base import ExecutionResult, require_approved, require_executed

SECRET = "outreach-smtp"


class EmailExecutor:
    action_types = ("send_pitch",)

    def __init__(self, smtp_factory=None):
        self.smtp_factory = smtp_factory or (lambda host, port: smtplib.SMTP(host, port, timeout=30))
        self.sent_this_run = 0

    def _send(self, to: str, subject: str, body: str) -> str:
        if self.sent_this_run >= get_settings().pitch_batch_cap:
            raise RuntimeError("pitch batch cap reached for this run (Section 6.5)")
        creds = json.loads(resolve_secret(SECRET))
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = creds["from"], to, subject
        msg.set_content(body)
        with self.smtp_factory(creds["host"], int(creds.get("port", 587))) as smtp:
            if creds.get("user"):
                smtp.starttls()
                smtp.login(creds["user"], creds["password"])
            smtp.send_message(msg)
        self.sent_this_run += 1
        return msg["Message-ID"] or ""

    def execute(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_approved(approval)
        p = approval["payload"]
        to = p.get("to") or p.get("contact_email")
        if not to:
            raise RuntimeError("pitch has no recipient address; the approver must supply one in the payload")
        self._send(to, p["subject"], p["body"])
        scope.execute("update pitches set status = 'sent' where project_id = %(project_id)s and id = %(id)s", {"id": p["pitch_id"]})
        return ExecutionResult(ok=True, detail={"to": to}, reversal_payload={"kind": "send_retraction", "pitch_id": p["pitch_id"], "to": to, "subject": p["subject"]})

    def reverse(self, scope: ProjectScope, project: dict[str, Any], approval: dict[str, Any], params: dict[str, Any]) -> ExecutionResult:
        require_executed(approval)
        rp = approval["reversal_payload"]
        self._send(rp["to"], f"Re: {rp['subject']}", "Please disregard the previous message; it was sent in error. Apologies for the noise.")
        scope.execute("update pitches set status = 'retracted' where project_id = %(project_id)s and id = %(id)s", {"id": rp["pitch_id"]})
        return ExecutionResult(ok=True, detail={"retracted_to": rp["to"]}, reversal_payload=rp)
