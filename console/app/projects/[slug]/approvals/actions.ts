"use server";

import { revalidatePath } from "next/cache";
import { projectBySlug, withProject } from "@/lib/db";

// The console decides approvals as the configured approver. The database records who decided; the
// worker's executors act only on rows with status = 'approved'. Nothing here executes anything.
export async function decideApproval(formData: FormData): Promise<void> {
  const slug = String(formData.get("slug") ?? "");
  const approvalId = String(formData.get("approval_id") ?? "");
  const decision = String(formData.get("decision") ?? "");
  const approver = process.env.CONSOLE_APPROVER_ID;
  if (!approver) throw new Error("CONSOLE_APPROVER_ID is not set");
  if (!["approve", "reject"].includes(decision)) throw new Error("bad decision");
  // A pitch is drafted without an address; the approver supplies the recipient when approving it.
  const recipient = String(formData.get("recipient") ?? "").trim();
  if (recipient && !/^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$/.test(recipient)) throw new Error("recipient is not an email address");
  const approverInput = decision === "approve" && recipient ? JSON.stringify({ to: recipient }) : null;
  const project = await projectBySlug(slug);
  if (!project) throw new Error("unknown project");
  if (String(project.approver_id) !== approver) throw new Error("this console is not the approver for this project");
  await withProject(String(project.id), async (q) => {
    const rows = await q(
      `update approvals set status = $3, approver_id = $4, decided_at = now(), approver_input = coalesce($5::jsonb, approver_input)
        where project_id = $1 and id = $2 and status = 'pending' returning id, run_id`,
      [approvalId, decision === "approve" ? "approved" : "rejected", approver, approverInput],
    );
    if (rows.length !== 1) throw new Error("approval was not pending");
    await q("insert into audit_log (project_id, run_id, actor, event, detail) values ($1, $2, $3, $4, $5)", [
      rows[0].run_id, `approver:${approver}`, decision === "approve" ? "approval_approved" : "approval_rejected", JSON.stringify({ approval_id: approvalId, via: "console", ...(recipient && decision === "approve" ? { recipient } : {}) }),
    ]);
  });
  revalidatePath(`/projects/${slug}/approvals`);
}
