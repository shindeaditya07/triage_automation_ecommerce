import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { MESSAGE_MAX, useSendContact } from "../api/contact";
import { useTitle } from "../hooks/useTitle";

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

export function ContactPage() {
  useTitle("Contact us");
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
    send.mutate({
      ...(name.trim() && { name: name.trim() }),
      email: email.trim(),
      ...(subject.trim() && { subject: subject.trim() }),
      message: message.trim(),
      ...(orderId.trim() && { order_id: orderId.trim() }),
      submission_id: submissionId,
      website,
    });
  }

  function again() {
    setMessage("");
    setSubject("");
    setSubmissionId(crypto.randomUUID());
    send.reset();
  }

  if (send.isSuccess) {
    return (
      <main id="main" tabIndex={-1} className="contact">
        <h1>Message sent</h1>
        <p role="status">
          Thank you. We have your message and will reply to {email.trim()} once we have looked at
          it.
        </p>
        <button type="button" onClick={again}>
          Send another message
        </button>
      </main>
    );
  }

  return (
    <main id="main" tabIndex={-1} className="contact">
      <h1>Contact us</h1>
      <p>
        Tell us what you need. If it is about an order, add the order number so we can find it
        quickly.
      </p>
      <form onSubmit={submit} noValidate>
        <label>
          Your name (optional)
          <input
            autoComplete="name"
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          Email address
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
        <button type="submit" disabled={!ready || send.isPending}>
          {send.isPending ? "Sending…" : "Send message"}
        </button>
      </form>
    </main>
  );
}
