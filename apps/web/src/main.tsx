import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createQueryClient } from "./api/queryClient";
import { App } from "./App";
import "./styles.css";
import { applySavedTheme } from "./theme";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Missing #root element");
}

applySavedTheme();
const queryClient = createQueryClient();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
