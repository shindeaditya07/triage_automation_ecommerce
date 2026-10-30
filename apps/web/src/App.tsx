import { useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { RequireOwner } from "./components/RequireOwner";
import { AuditPage } from "./pages/AuditPage";
import { ContactPage } from "./pages/ContactPage";
import { LoginPage } from "./pages/LoginPage";
import { MetricsPage } from "./pages/MetricsPage";
import { QueuePage } from "./pages/QueuePage";
import { TicketPage } from "./pages/TicketPage";
import { UploadPage } from "./pages/UploadPage";

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

/** The owner pages, with Metrics as the home page and the ticket queue at /tickets. */
export function AppRoutes() {
  return (
    <>
      <SkipLink />
      <FocusOnNavigate />
      <Routes>
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireOwner>
              <MetricsPage />
            </RequireOwner>
          }
        />
        <Route
          path="/tickets"
          element={
            <RequireOwner>
              <QueuePage />
            </RequireOwner>
          }
        />
        <Route
          path="/upload"
          element={
            <RequireOwner>
              <UploadPage />
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
        <Route path="/metrics" element={<Navigate to="/" replace />} />
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
    </>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
