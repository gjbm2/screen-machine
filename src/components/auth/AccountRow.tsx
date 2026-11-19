// The menu's "You" row (docs/ACCOUNTS.md): who is signed in and Sign out, or Google's sign-in button. Shown only once
// the deployment has sign-in set up.
import { toast } from "sonner";
import { signOut, useViewer } from "@/shared/auth/viewer";
import { GoogleSignIn } from "./GoogleSignIn";

const ROLE_WORDS = { admin: "Admin", member: "Member", guest: "Guest" } as const;

export function AccountRow() {
  const viewer = useViewer();
  if (!viewer.accounts) return null;
  return (
    <section aria-label="You" className="space-y-2 border-b border-stone-200/80 pb-4" data-testid="app-menu-account">
      {viewer.signed_in ? (
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold">{viewer.name || viewer.email}</div>
            <div className="truncate text-[13px] text-stone-500">
              {ROLE_WORDS[viewer.role]} · {viewer.email}
            </div>
          </div>
          <button
            type="button"
            className="min-h-11 shrink-0 rounded-xl border border-stone-300 px-3 text-[14px] hover:bg-stone-100"
            onClick={async () => {
              try {
                await signOut();
                window.location.reload();
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Sign-out failed");
              }
            }}
          >
            Sign out
          </button>
        </div>
      ) : (
        <div className="space-y-1.5">
          <p className="text-[13px] text-stone-500">Sign in to keep your pictures on every device.</p>
          <GoogleSignIn />
        </div>
      )}
    </section>
  );
}
