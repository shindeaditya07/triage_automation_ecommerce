import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { MESSAGE_MAX, useSendContact } from "../api/contact";

// The same shape the server accepts for an order number (services/api/intake/cleaning.py).
const ORDER_ID = /^[A-Za-z0-9][A-Za-z0-9_/#-]{0,49}$/;

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429)
      return "You have sent several messages already. Please wait a little, then try again.";
    if (error.status === 413) return "That message is too long to send. Please shorten it.";
    if (error.status === 422) return "Please check your email address and message, then try again.";
    if (error.status === 503) return "We cannot take messages right now. Please try again later.";
  }
  return "We could not send your message. Please check your connection and try again.";
}

interface Props {
  /** Who is typing: a customer, or the owner recording a complaint for one. */
  mode: "customer" | "owner";
  /** Called once the server has accepted a message. */
  onSent?: () => void;
}

export function ContactForm({ mode, onSent }: Props) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [orderId, setOrderId] = useState("");
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [website, setWebsite] = useState("");
  // One id per page load: a double click or a retry after a failure is still one ticket.
  const [submissionId, setSubmissionId] = useState(() => crypto.randomUUID());
  const send = useSendContact();

  const orderProblem = orderId.trim() !== "" && !ORDER_ID.test(orderId.trim());
  const ready = email.trim() !== "" && message.trim() !== "" && !orderProblem;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || send.isPending) return;
    send.mutate(
      {
        ...(name.trim() && { name: name.trim() }),
        email: email.trim(),
        ...(subject.trim() && { subject: subject.trim() }),
        message: message.trim(),
        ...(orderId.trim() && { order_id: orderId.trim() }),
        submission_id: submissionId,
        website,
      },
      { onSuccess: () => onSent?.() },
    );
  }

  function again() {
    setMessage("");
    setSubject("");
    if (mode === "owner") {
      setName("");
      setEmail("");
      setOrderId("");
    }
    setSubmissionId(crypto.randomUUID());
    send.reset();
  }

  if (send.isSuccess) {
    return (
      <div className="contact-done">
        <p role="status">
          {mode === "owner"
            ? "Complaint recorded. It is now in the queue and will be drafted in a minute or so."
            : `Thank you. We have your message and will reply to ${email.trim()} once we have looked at it.`}
        </p>
        <div className="actions">
          <button type="button" onClick={again}>
            {mode === "owner" ? "Add another complaint" : "Send another message"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
      {mode === "customer" && (
        <p>
          Tell us what you need. If it is about an order, add the order number so we can find it
          quickly.
        </p>
      )}
      <form onSubmit={submit} noValidate>
        <label>
          {mode === "owner" ? "Customer's name (optional)" : "Your name (optional)"}
          <input
            autoComplete="name"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          {mode === "owner" ? "Customer's email address" : "Email address"}
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Order number (optional)
          <input
            autoComplete="off"
            maxLength={50}
            aria-invalid={orderProblem}
            aria-describedby={orderProblem ? "order-problem" : undefined}
            value={orderId}
            onChange={(e) => setOrderId(e.target.value)}
          />
        </label>
        {orderProblem && (
          <p id="order-problem" className="error">
            Order numbers use letters, digits and - _ / # only.
          </p>
        )}
        <label>
          Subject (optional)
          <input maxLength={200} value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label>
          Message
          <textarea
            required
            rows={6}
            maxLength={MESSAGE_MAX}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </label>
        <p className="hint">
          {message.length} of {MESSAGE_MAX} characters. Please do not send card numbers or
          passwords.
        </p>
        <div className="trap" aria-hidden="true">
          <label>
            Leave this empty
            <input
              tabIndex={-1}
              autoComplete="off"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </label>
        </div>
        {send.isError && (
          <p className="error" role="alert">
            {messageFor(send.error)}
          </p>
        )}
        <div className="actions">
          <button type="submit" disabled={!ready || send.isPending}>
            {send.isPending ? "Sending…" : mode === "owner" ? "Add complaint" : "Send message"}
          </button>
        </div>
      </form>
    </>
  );
}
