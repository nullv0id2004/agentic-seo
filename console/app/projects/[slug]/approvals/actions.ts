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
  const project = await projectBySlug(slug);
  if (!project) throw new Error("unknown project");
  if (String(project.approver_id) !== approver) throw new Error("this console is not the approver for this project");
  await withProject(String(project.id), async (q) => {
    const rows = await q(
      "update approvals set status = $3, approver_id = $4, decided_at = now() where project_id = $1 and id = $2 and status = 'pending' returning id, run_id",
      [approvalId, decision === "approve" ? "approved" : "rejected", approver],
    );
    if (rows.length !== 1) throw new Error("approval was not pending");
    await q("insert into audit_log (project_id, run_id, actor, event, detail) values ($1, $2, $3, $4, $5)", [
      rows[0].run_id, `approver:${approver}`, decision === "approve" ? "approval_approved" : "approval_rejected", JSON.stringify({ approval_id: approvalId, via: "console" }),
    ]);
  });
  revalidatePath(`/projects/${slug}/approvals`);
}
