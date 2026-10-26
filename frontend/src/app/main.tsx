import { QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import "@/styles/ui.css";
import { App } from "./App";
import { queryClient } from "./queryClient";

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>,
);
