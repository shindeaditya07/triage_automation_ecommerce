import { useState } from "react";

import { useOwnerReply } from "../api/tickets";
import { errorMessage, warningsOf } from "../ticketView";

interface Props {
  ticketId: string;
  originalViewed: boolean;
}

/**
 * Answer an escalated ticket yourself. The system wrote no draft, so this is your text. It is
 * checked like an edit: a warning does not stop you, but sending past one needs a reason. Nothing
 * leaves the system; the reply is recorded and mock-sent like any approval.
 */
export function OwnerReply({ ticketId, originalViewed }: Props) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const reply = useOwnerReply(ticketId);
  const warnings = warningsOf(reply.error);
  const rules = [...new Set(warnings.map((w) => w.ruleId))];
  const needsReason = warnings.length > 0;
  const canSend =
    text.trim() !== "" &&
    originalViewed &&
    (!needsReason || reason.trim() !== "") &&
    !reply.isPending;

  return (
    <form
      className="editor"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canSend) return;
        reply.mutate({
          reply_text: text,
          original_viewed: originalViewed,
          override: needsReason ? { reason: reason.trim(), overridden_rules: rules } : null,
        });
      }}
    >
      <p className="hint">
        The system wrote no draft for this ticket. Write your own reply. The store sign-off is added
        for you. Nothing is emailed: sending records a mock send.
      </p>
      <label>
        Your reply to the customer
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (reply.isError) reply.reset();
          }}
          rows={10}
        />
      </label>
      {!originalViewed && (
        <p className="warn-text">Open the customer&apos;s original message before replying.</p>
      )}
      {needsReason && (
        <div role="alert">
          <p className="warn-text">These claims are not backed by the order or policy:</p>
          <ul>
            {warnings.map((w, i) => (
              <li key={i}>
                <strong>{w.ruleId}</strong> {w.detail}
              </li>
            ))}
          </ul>
          <label>
            Why is it right to send this anyway?
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
          </label>
        </div>
      )}
      {reply.isError && !needsReason && (
        <p className="error" role="alert">
          {errorMessage(reply.error)}
        </p>
      )}
      <div className="actions">
        <button type="submit" disabled={!canSend}>
          {needsReason ? "Send anyway (mock)" : "Send reply (mock)"}
        </button>
      </div>
    </form>
  );
}
