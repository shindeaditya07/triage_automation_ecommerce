import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useLogout, useMe } from "../api/auth";
import { useTheme } from "../theme";

/** The header every owner page shares: where to go, who is signed in, and how to sign out. */
export function TopBar({ children }: { children?: ReactNode }) {
  const me = useMe();
  const logout = useLogout();
  const { theme, toggle } = useTheme();
  return (
    <header className="topbar">
      <span className="brand">
        <h1>ReplyDesk</h1>
        <button
          type="button"
          className="theme-toggle"
          onClick={toggle}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        >
          {theme === "dark" ? "☀ Light" : "☾ Dark"}
        </button>
      </span>
      <span className="who">
        <NavLink to="/" end>
          Metrics
        </NavLink>
        <NavLink to="/tickets" end>
          Tickets
        </NavLink>
        {children}
        {me.data?.email}{" "}
        <button type="button" onClick={() => logout.mutate()}>
          Sign out
        </button>
      </span>
    </header>
  );
}
