import { useState } from "react";

import { useApprove, useReject } from "../api/tickets";
import { errorMessage } from "../ticketView";
import { Modal } from "./Modal";

const MIN_REASON = 10;

interface ApproveProps {
  ticketId: string;
  originalViewed: boolean;
  suggestionOnly: boolean;
  onClose: () => void;
}

export function ApproveDialog({ ticketId, originalViewed, suggestionOnly, onClose }: ApproveProps) {
  const [confirmed, setConfirmed] = useState(false);
  const approve = useApprove(ticketId);
  const ready = originalViewed && (!suggestionOnly || confirmed);
  return (
    <Modal title="Approve this reply?" onClose={onClose}>
      <p>Approving records a mock send. Nothing leaves the system.</p>
      {!originalViewed && (
        <p className="warn-text">
          Open the customer&apos;s original message first. This ticket cannot be approved without
          it.
        </p>
      )}
      {suggestionOnly && (
        <label className="check">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          This draft is only a suggestion and I have checked it myself.
        </label>
      )}
      {approve.isError && (
        <p className="error" role="alert">
          {errorMessage(approve.error)}
        </p>
      )}
      <div className="actions">
        <button
          type="button"
          disabled={!ready || approve.isPending}
          onClick={() =>
            approve.mutate(
              { original_viewed: originalViewed, confirm_suggestion_only: confirmed },
              { onSuccess: onClose },
            )
          }
        >
          Approve
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

export function RejectDialog({ ticketId, onClose }: { ticketId: string; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const reject = useReject(ticketId);
  const ready = reason.trim().length >= MIN_REASON;
  return (
    <Modal title="Reject this reply?" onClose={onClose}>
      <label>
        Why? (kept in the audit log)
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
      </label>
      {!ready && <p className="hint">At least {MIN_REASON} characters.</p>}
      {reject.isError && (
        <p className="error" role="alert">
          {errorMessage(reject.error)}
        </p>
      )}
      <div className="actions">
        <button
          type="button"
          disabled={!ready || reject.isPending}
          onClick={() => reject.mutate(reason.trim(), { onSuccess: onClose })}
        >
          Reject
        </button>
        <button type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
