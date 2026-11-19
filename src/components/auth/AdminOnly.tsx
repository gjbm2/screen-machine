// A setup page (the Scheduler, Jobs, Settings, the admin and developer pages) for admins only, once the deployment has
// sign-in set up (docs/ACCOUNTS.md). Anyone else gets a short note and the sign-in button. Flask refuses their API calls
// anyway (config/auth/gates.yaml); this keeps them from a page that would only fail.
import type { ReactNode } from "react";
import { AppLink } from "@/components/nav/AppLink";
import { atLeast, useViewer } from "@/shared/auth/viewer";
import { GoogleSignIn } from "./GoogleSignIn";

export function AdminOnly({ children }: { children: ReactNode }) {
  const viewer = useViewer();
  if (atLeast(viewer, "admin")) return <>{children}</>;
  return (
    <main className="mx-auto flex min-h-[60vh] max-w-sm flex-col justify-center gap-4 px-4 text-center">
      <h1 className="text-xl font-semibold">{viewer.signed_in ? "This page is for the house's admins" : "Sign in to see this"}</h1>
      <p className="text-[15px] text-muted-foreground">
        {viewer.signed_in
          ? `You're signed in as ${viewer.email}, which can't change the house's setup.`
          : "This page changes the house's setup. Sign in with an admin's Google account."}
      </p>
      {!viewer.signed_in && <div className="flex justify-center"><GoogleSignIn /></div>}
      <AppLink href="/" className="text-[15px] underline underline-offset-4">Go to the generator</AppLink>
    </main>
  );
}
