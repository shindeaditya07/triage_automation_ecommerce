import { useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { RequireOwner } from "./components/RequireOwner";
import { AuditPage } from "./pages/AuditPage";
import { LoginPage } from "./pages/LoginPage";
import { MetricsPage } from "./pages/MetricsPage";
import { QueuePage } from "./pages/QueuePage";
import { TicketPage } from "./pages/TicketPage";

/** A keyboard user's way past the header on every page. */
function SkipLink() {
  return (
    <a className="skip-link" href="#main">
      Skip to content
    </a>
  );
}

/** After moving to another page, focus goes to its content so a screen reader starts there. */
function FocusOnNavigate() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    document.getElementById("main")?.focus();
  }, [pathname]);
  return null;
}

export function App() {
  return (
    <BrowserRouter>
      <SkipLink />
      <FocusOnNavigate />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireOwner>
              <QueuePage />
            </RequireOwner>
          }
        />
        <Route
          path="/audit/:ticketId?"
          element={
            <RequireOwner>
              <AuditPage />
            </RequireOwner>
          }
        />
        <Route
          path="/metrics"
          element={
            <RequireOwner>
              <MetricsPage />
            </RequireOwner>
          }
        />
        <Route
          path="/tickets/:ticketId"
          element={
            <RequireOwner>
              <TicketPage />
            </RequireOwner>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
