import { Suspense, lazy } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { queryClient } from "@/lib/queryClient";
import { Toaster } from "@/components/ui/sonner";

// Keep the router devtools out of the production bundle entirely.
const RouterDevtools = import.meta.env.DEV
  ? lazy(() =>
      import("@tanstack/react-router-devtools").then((m) => ({
        default: m.TanStackRouterDevtools,
      })),
    )
  : () => null;

const navLinkClass = "border-b-2 border-transparent pb-1 text-sm font-medium text-neutral-400 hover:text-neutral-100";
const navLinkActiveProps = {
  className: "border-b-2 border-emerald-400 pb-1 text-sm font-medium text-neutral-100",
};

export const Route = createRootRoute({
  component: RootComponent,
});

function RootComponent() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-background text-foreground">
        <header className="border-b border-border">
          <nav className="mx-auto flex max-w-5xl items-baseline gap-6 px-4 py-3">
            <Link to="/" className="font-serif text-lg font-semibold">
              stonks
            </Link>
            <div className="flex gap-4">
              <Link
                to="/"
                className={navLinkClass}
                activeOptions={{ exact: true }}
                activeProps={navLinkActiveProps}
              >
                Discover
              </Link>
              <Link to="/watchlist" className={navLinkClass} activeProps={navLinkActiveProps}>
                Watchlist
              </Link>
              <Link to="/portfolio" className={navLinkClass} activeProps={navLinkActiveProps}>
                Portfolio
              </Link>
            </div>
          </nav>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-8">
          <Outlet />
        </main>
      </div>
      <Suspense>
        <RouterDevtools />
      </Suspense>
      <Toaster />
    </QueryClientProvider>
  );
}
