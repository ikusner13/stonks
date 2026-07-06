import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider, createRouter, type ErrorComponentProps } from "@tanstack/react-router";
import "@fontsource/source-sans-3/400.css";
import "@fontsource/source-sans-3/600.css";
import "@fontsource/source-serif-4/600.css";
import "./index.css";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/common";
import { apiErrorMessage } from "@/api/errors";
import { routeTree } from "./routeTree.gen";

// Cloudflare deploys replace the built asset chunks; a tab open across a deploy
// can request a chunk that no longer exists. Reload once per URL to pick up the
// new build instead of crashing — a second failure on the same URL propagates
// to the router's error component instead of reload-looping.
window.addEventListener("vite:preloadError", (event) => {
  const key = `chunk-reload:${window.location.href}`;
  if (!sessionStorage.getItem(key)) {
    sessionStorage.setItem(key, "1");
    event.preventDefault();
    window.location.reload();
  }
});

function RootPending() {
  return (
    <div className="flex min-h-[50vh] items-center justify-center">
      <Spinner className="size-6" />
    </div>
  );
}

function RootError({ error, reset }: ErrorComponentProps) {
  return (
    <div className="flex min-h-[50vh] items-center justify-center p-4">
      <div className="w-full max-w-md rounded-lg border border-destructive/40 bg-destructive/10 p-6 text-center">
        <h1 className="text-lg font-semibold">Something broke</h1>
        <p className="mt-2 text-sm text-muted-foreground">{apiErrorMessage(error)}</p>
        <div className="mt-4 flex justify-center gap-2">
          <Button variant="outline" onClick={() => window.location.reload()}>
            Reload page
          </Button>
          <Button onClick={() => reset()}>Try again</Button>
        </div>
      </div>
    </div>
  );
}

const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  defaultPendingComponent: RootPending,
  defaultErrorComponent: RootError,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
