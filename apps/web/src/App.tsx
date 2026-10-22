import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { RequireOwner } from "./components/RequireOwner";
import { LoginPage } from "./pages/LoginPage";
import { MetricsPage } from "./pages/MetricsPage";
import { QueuePage } from "./pages/QueuePage";
import { TicketPage } from "./pages/TicketPage";

export function App() {
  return (
    <BrowserRouter>
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
