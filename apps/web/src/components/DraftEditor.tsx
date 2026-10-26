import { useEffect, useMemo, useState } from "react";

import { useSaveEdit, useValidateDraft, type RuleFailure } from "../api/tickets";
import { errorMessage } from "../ticketView";

const DEBOUNCE_MS = 300;

interface Props {
  ticketId: string;
  initial: string;
  onDone: () => void;
}

/**
 * Edit the reply. Each pause in typing asks the server for the grounding warnings. They never
 * block saving, but saving with a warning needs a reason, which the server records as an override.
 */
export function DraftEditor({ ticketId, initial, onDone }: Props) {
  const [text, setText] = useState(initial);
  const [reason, setReason] = useState("");
  const [checked, setChecked] = useState<{ text: string; failures: RuleFailure[] } | null>(null);
  const validate = useValidateDraft(ticketId);
  const save = useSaveEdit(ticketId);
  const { mutate: runValidate } = validate;

  useEffect(() => {
    if (!text.trim()) return;
    const timer = setTimeout(
      () =>
        runValidate(text, {
          onSuccess: (result) => setChecked({ text, failures: result.failures }),
        }),
      DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [text, runValidate]);

  const current = checked?.text === text ? checked.failures : null;
  const rules = useMemo(() => [...new Set((current ?? []).map((f) => f.rule_id))], [current]);
  const needsReason = rules.length > 0;
  const canSave =
    text.trim() !== "" &&
    current !== null &&
    (!needsReason || reason.trim() !== "") &&
    !save.isPending;

  return (
    <form
      className="editor"
      onSubmit={(event) => {
        event.preventDefault();
        if (!canSave) return;
        save.mutate(
          {
            reply_text: text,
            override: needsReason ? { reason: reason.trim(), overridden_rules: rules } : null,
          },
          { onSuccess: onDone },
        );
      }}
    >
      <label>
        Reply to the customer
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12} autoFocus />
      </label>
      <div role="status" aria-live="polite" className="validation">
        {current === null && text.trim() !== "" && <p>Checking the reply…</p>}
        {current !== null && current.length === 0 && <p className="ok">No grounding warnings.</p>}
        {current !== null && current.length > 0 && (
          <>
            <p className="warn-text">These claims are not backed by the order or policy:</p>
            <ul>
              {current.map((f, i) => (
                <li key={i}>
                  <strong>{f.rule_id}</strong> {f.detail}
                </li>
              ))}
            </ul>
          </>
        )}
        {validate.isError && <p className="error">Could not check the reply. Try typing again.</p>}
      </div>
      {needsReason && (
        <label>
          Why is it right to send this anyway?
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
        </label>
      )}
      {save.isError && (
        <p className="error" role="alert">
          {errorMessage(save.error)}
        </p>
      )}
      <div className="actions">
        <button type="submit" disabled={!canSave}>
          Save edit
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}
