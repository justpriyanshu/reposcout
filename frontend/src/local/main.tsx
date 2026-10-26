import { QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import "@/styles/ui.css";
import { queryClient } from "@/app/queryClient";
import { LocalApp } from "./LocalApp";

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={queryClient}>
    <LocalApp />
  </QueryClientProvider>,
);
