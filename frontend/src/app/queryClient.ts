import { QueryClient } from "@tanstack/react-query";

// Requests are never retried or refetched behind the visitor's back: Ask and Review spend their
// API quota, and the views show their own error states with a "Try again".
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false },
    mutations: { retry: false },
  },
});
