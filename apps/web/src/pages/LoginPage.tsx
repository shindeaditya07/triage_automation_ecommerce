import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { useLogin, useMe } from "../api/auth";
import { ApiError } from "../api/client";
import { useTitle } from "../hooks/useTitle";

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "That email or password is not right.";
    if (error.status === 429) return "Too many attempts. Wait a little, then try again.";
    if (error.status === 503) return "Sign-in is not available on the server yet.";
  }
  return "Could not reach the server. Try again.";
}

export function LoginPage() {
  useTitle("Sign in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();
  const me = useMe();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (me.data) return <Navigate to={from} replace />;

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => navigate(from, { replace: true }) });
  }

  return (
    <main id="main" tabIndex={-1} className="login">
      <h1>ReplyDesk</h1>
      <form onSubmit={submit}>
        <label>
          Email
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {login.isError && (
          <p className="error" role="alert">
            {messageFor(login.error)}
          </p>
        )}
        <button type="submit" disabled={login.isPending}>
          {login.isPending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
