// Settings › People (#people; docs/ACCOUNTS.md): who may do what, once the deployment has sign-in set up. Everyone who
// has signed in is listed with a role to pick: Guest (their own pictures; the screens open to guests), Approved (also
// the deployment's private workflows) or Admin (everything, everyone's pictures, this page). Someone who hasn't signed
// in yet can be added by e-mail. Writes go to the deployment's private accounts file; the last admin can't be removed.

import { useEffect, useState } from "react";
import { Users } from "lucide-react";
import { toast } from "sonner";
import { listPeople, setPersonRole, useViewer, type PeopleList, type Role } from "@/shared/auth/viewer";
import { Chips, Group, Hint } from "./Group";

const ROLE_OPTIONS: { value: Role; label: string }[] = [
  { value: "guest", label: "Guest" },
  { value: "approved", label: "Approved" },
  { value: "admin", label: "Admin" },
];

function seen(t: number | null): string {
  if (!t) return "Hasn't signed in yet";
  const d = new Date(t * 1000);
  const days = Math.floor((Date.now() - d.getTime()) / 86400000);
  if (days < 1) return "Last here today";
  if (days < 2) return "Last here yesterday";
  return `Last here ${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}`;
}

export function PeopleGroup() {
  const viewer = useViewer();
  if (!viewer.accounts) return null;
  return (
    <Group id="people" title="People" icon={<Users />} summary="Who can do what">
      <PeopleBody />
    </Group>
  );
}

/** Mounted only while the group is open, so a closed group costs no requests. */
function PeopleBody() {
  const [list, setList] = useState<PeopleList | null>(null);
  const [failed, setFailed] = useState(false);
  const [adding, setAdding] = useState("");

  useEffect(() => {
    let alive = true;
    listPeople()
      .then((l) => alive && setList(l))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const change = async (email: string, role: Role) => {
    try {
      setList(await setPersonRole(email, role));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not change the role");
    }
  };

  const add = async () => {
    const email = adding.trim().toLowerCase();
    if (!email.includes("@")) return;
    await change(email, "approved");
    setAdding("");
  };

  return (
    <>
      {failed && <Hint>The app isn't answering, so the people can't be read.</Hint>}
      <ul className="divide-y divide-stone-200/80 rounded-2xl border border-stone-200/80 bg-card" data-testid="settings-people">
        {(list?.people ?? []).map((p) => (
          <li key={p.email} className="space-y-2 px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-base font-medium leading-snug">
                {p.name || p.email}
                {p.email === list?.me && <span className="text-muted-foreground"> (you)</span>}
              </p>
              <p className="truncate text-sm text-muted-foreground">
                {p.name ? `${p.email} · ` : ""}
                {seen(p.last_seen)}
              </p>
            </div>
            <Chips label={`${p.name || p.email}'s role`} value={p.role} options={ROLE_OPTIONS} onChange={(r) => void change(p.email, r)} />
          </li>
        ))}
        {list && list.people.length === 0 && <li className="px-3 py-2 text-[15px] text-muted-foreground">Nobody has signed in yet.</li>}
      </ul>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void add();
        }}
      >
        <input
          type="email"
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          placeholder="Add someone by their Google e-mail"
          aria-label="Add someone by their Google e-mail"
          className="min-h-11 min-w-0 flex-1 rounded-xl border border-stone-300 bg-white px-3 text-[15px]"
        />
        <button type="submit" className="min-h-11 shrink-0 rounded-xl bg-teal-700 px-4 text-[15px] font-medium text-white disabled:opacity-50" disabled={!adding.includes("@")}>
          Approve
        </button>
      </form>
      <Hint>
        Guests make their own pictures and use the screens open to guests. Approved people also get the deployment's
        private workflows. Admins get everything, including this page and everyone's pictures. Anyone who signs in with
        Google starts as a guest.
      </Hint>
    </>
  );
}
