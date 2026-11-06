import Link from "next/link";
import { notFound } from "next/navigation";

import { ActionForm } from "@/components/ActionForm";
import { Badge, PageHead } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { formatTime } from "@/lib/format";
import type { Approval } from "@/lib/types";
import { decideApproval } from "../../../actions";

export const metadata = { title: "Approval - Mabeyn Admin" };

export default async function ApprovalPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ decided?: string }>;
}) {
  const { id } = await params;
  const { decided } = await searchParams;
  const approval = await api<Approval>(`/approvals/${id}`).catch((error) => {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  });
  const payload = approval.payload;

  return (
    <>
      <PageHead title="Approval request" hint={payload.summary}>
        <Badge value={approval.status} />
      </PageHead>
      {decided && (
        <p className="ok" role="status">
          Decision recorded ({decided === "approve" ? "approved" : "rejected"}). The customer's conversation has continued.
        </p>
      )}
      <div className="stack">
        <section className="card">
          <dl className="kv">
            <dt>Action</dt><dd><code>{payload.tool}</code></dd>
            <dt>Customer</dt><dd>{payload.customer_id}</dd>
            <dt>Requested</dt><dd>{formatTime(approval.created_at)}</dd>
            <dt>Expires</dt><dd>{formatTime(approval.expires_at)}</dd>
            {approval.decided_at && (<><dt>Decided</dt><dd>{formatTime(approval.decided_at)}</dd></>)}
            <dt>Conversation</dt><dd><Link href={`/conversations/${approval.conversation_id}`}>Open the conversation</Link></dd>
            <dt>Arguments</dt><dd><pre>{JSON.stringify(payload.arguments ?? {}, null, 2)}</pre></dd>
          </dl>
        </section>
        {approval.status === "pending" ? (
          <section className="card">
            <h2>Decision</h2>
            <ActionForm action={decideApproval} resetOnSuccess={false}>
              <input type="hidden" name="id" value={approval.id} />
              <label>
                Note for the customer's conversation (optional)
                <input type="text" name="note" maxLength={500} />
              </label>
              <div className="row">
                <button type="submit" name="decision" value="approve">Approve and run</button>
                <button type="submit" name="decision" value="reject" className="danger">Reject</button>
              </div>
            </ActionForm>
          </section>
        ) : (
          <p className="muted">This request was {approval.status}; it can no longer be decided.</p>
        )}
      </div>
    </>
  );
}
