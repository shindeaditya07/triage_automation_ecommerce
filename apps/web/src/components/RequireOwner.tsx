import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useMe } from "../api/auth";
import { ApiError } from "../api/client";

/** Shows its children to a signed-in owner and sends everyone else to the login page. */
export function RequireOwner({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending)
    return (
      <p className="status" role="status">
        Checking your session…
      </p>
    );
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (me.isError) {
    return (
      <p className="status error" role="alert">
        Could not reach the server.{" "}
        <button type="button" onClick={() => void me.refetch()}>
          Try again
        </button>
      </p>
    );
  }
  return <>{children}</>;
}
