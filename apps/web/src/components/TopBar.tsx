import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useLogout, useMe } from "../api/auth";

/** The header every owner page shares: where to go, who is signed in, and how to sign out. */
export function TopBar({ children }: { children?: ReactNode }) {
  const me = useMe();
  const logout = useLogout();
  return (
    <header className="topbar">
      <h1>ReplyDesk</h1>
      <span className="who">
        <NavLink to="/" end>
          Metrics
        </NavLink>
        <NavLink to="/tickets" end>
          Tickets
        </NavLink>
        <NavLink to="/audit">Audit log</NavLink>
        {children}
        {me.data?.email}{" "}
        <button type="button" onClick={() => logout.mutate()}>
          Sign out
        </button>
      </span>
    </header>
  );
}
