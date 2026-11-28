// Settings › Apps & API keys (#api; docs/API.md): keys for other apps and AI agents to make pictures through the
// generation API (/api/v1). Shown where the deployment has the `api` capability. A new key's whole value is shown once,
// with a copy button and a ready curl line; after that only its hint. Each key says what it may do (make pictures, or
// also show them on chosen screens), its limits, when it was last used and from where, and when it runs out; it can
// be changed, given a new secret (the old one stopping now or in 24 hours) or revoked (at once, for good). What a key
// makes lands in the Generated tab of whoever made the key.

import { useEffect, useState, type ReactNode } from "react";
import { Copy, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useDeployment } from "@/hooks/useDeployment";
import { changeKey, listKeys, makeKey, revokeKey, rollKey, type ApiKey, type KeyFields, type KeyList } from "@/shared/auth/keys";
import { Chips, Group, Hint, LinkRow, Row, Sub } from "./Group";

const PER_DAY = [
  { value: "25", label: "25" },
  { value: "100", label: "100" },
  { value: "500", label: "500" },
  { value: "0", label: "No limit" },
];
const AT_ONCE = [
  { value: "1", label: "1" },
  { value: "3", label: "3" },
  { value: "10", label: "10" },
  { value: "0", label: "No limit" },
];
const EXPIRES = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "Never" },
];

const apiBase = () => `${window.location.origin}/api/v1`;

function ago(t: number | null): string {
  if (!t) return "";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

const day = (t: number) => new Date(t * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied");
  } catch {
    toast.error("Couldn't copy here: select the text and copy it");
  }
}

function Button({ children, onClick, tone = "plain", disabled, testId }: { children: ReactNode; onClick: () => void; tone?: "plain" | "primary" | "danger"; disabled?: boolean; testId?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "inline-flex min-h-11 items-center gap-1.5 rounded-xl px-4 text-[15px] font-medium disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        tone === "primary" && "bg-teal-700 text-white",
        tone === "danger" && "bg-red-700 text-white",
        tone === "plain" && "border border-stone-300 bg-white text-stone-700 hover:bg-stone-50",
      )}
    >
      {children}
    </button>
  );
}

export function ApiKeysGroup() {
  const { deployment } = useDeployment();
  if (deployment?.capabilities?.api !== true) return null;
  return (
    <Group id="api" title="Apps & API keys" icon={<KeyRound />} summary="Let other apps and AI agents make pictures">
      <ApiKeysBody />
    </Group>
  );
}

/** Mounted only while the group is open. */
function ApiKeysBody() {
  const [list, setList] = useState<KeyList | null>(null);
  const [failed, setFailed] = useState(false);
  const [making, setMaking] = useState(false);
  const [fresh, setFresh] = useState<{ name: string; secret: string } | null>(null);

  useEffect(() => {
    let alive = true;
    listKeys()
      .then((l) => alive && setList(l))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, []);

  const live = (list?.keys ?? []).filter((k) => k.state === "active");
  const gone = (list?.keys ?? []).filter((k) => k.state !== "active");
  const screenName = (id: string) => list?.destinations.find((d) => d.id === id)?.name ?? id;

  return (
    <>
      <Hint>
        Other apps and AI agents can make pictures here with a key. What they make goes into your Generated pictures, and
        only onto a screen if you allow it.
      </Hint>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 break-all rounded-lg bg-stone-100 px-2 py-1 text-sm" data-testid="api-base">
          {apiBase()}
        </code>
        <button type="button" onClick={() => void copy(apiBase())} className="inline-flex min-h-11 items-center gap-1 px-2 text-[15px] text-teal-800" aria-label="Copy the API address">
          <Copy className="h-4 w-4" aria-hidden /> Copy
        </button>
      </div>
      <LinkRow href="/api/v1/openapi.json" newTab>
        The API's description, for developers and agents
      </LinkRow>

      {failed && <Hint>The app isn't answering, so the keys can't be read.</Hint>}

      {fresh && <FreshKey name={fresh.name} secret={fresh.secret} onDone={() => setFresh(null)} />}

      {making ? (
        <KeyForm
          list={list}
          onCancel={() => setMaking(false)}
          onSave={async (fields) => {
            const r = await makeKey(fields);
            setList(r);
            setMaking(false);
            setFresh({ name: r.key.name, secret: r.secret });
          }}
        />
      ) : (
        <Button tone="primary" onClick={() => setMaking(true)} testId="api-new-key">
          New key
        </Button>
      )}

      {list && (
        <ul className="space-y-3" data-testid="api-keys">
          {live.map((k) => (
            <KeyCard key={k.id} k={k} list={list} screenName={screenName} onList={setList} onSecret={(secret) => setFresh({ name: k.name, secret })} />
          ))}
          {live.length === 0 && !making && <li className="text-[15px] text-muted-foreground">No keys yet.</li>}
        </ul>
      )}

      {gone.length > 0 && (
        <details className="rounded-2xl border border-stone-200/80 bg-card px-3 py-2">
          <summary className="min-h-9 cursor-pointer text-[15px] text-muted-foreground">Revoked and expired ({gone.length})</summary>
          <ul className="mt-2 space-y-1">
            {gone.map((k) => (
              <li key={k.id} className="text-[15px] text-muted-foreground">
                {k.name} <span className="font-mono text-sm">{k.hint}</span> ·{" "}
                {k.state === "revoked" && k.revoked_at ? `revoked ${day(k.revoked_at)}` : `expired ${k.expires_at ? day(k.expires_at) : ""}`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function FreshKey({ name, secret, onDone }: { name: string; secret: string; onDone: () => void }) {
  const curl = `curl -s ${apiBase()}/generations -H "Authorization: Bearer ${secret}" -H "Content-Type: application/json" -d '{"prompt": "a lighthouse at dusk", "wait": 60}'`;
  return (
    <div className="space-y-2 rounded-2xl border-2 border-amber-400 bg-amber-50 p-3" data-testid="api-fresh-key">
      <p className="text-base font-semibold">{name}: copy the key now</p>
      <p className="text-[15px] text-stone-700">It is shown only this once. Keep it like a password; if it leaks, revoke it.</p>
      <div className="flex gap-2">
        <input readOnly value={secret} onFocus={(e) => e.currentTarget.select()} aria-label="The new key" className="min-h-11 min-w-0 flex-1 rounded-xl border border-stone-300 bg-white px-3 font-mono text-sm" data-testid="api-secret" />
        <Button tone="primary" onClick={() => void copy(secret)}>
          <Copy className="h-4 w-4" aria-hidden /> Copy
        </Button>
      </div>
      <details>
        <summary className="min-h-9 cursor-pointer text-[15px] text-teal-800">Try it</summary>
        <pre className="mt-1 whitespace-pre-wrap break-all rounded-lg bg-white p-2 text-xs">{curl}</pre>
        <button type="button" onClick={() => void copy(curl)} className="min-h-11 text-[15px] text-teal-800">
          Copy this line
        </button>
      </details>
      <Button onClick={onDone}>I've copied it</Button>
    </div>
  );
}

function what(k: ApiKey, screenName: (id: string) => string): string {
  const parts = [k.scopes.includes("publish") && k.destinations.length ? `Makes pictures and can show them on ${k.destinations.map(screenName).join(", ")}` : "Makes pictures"];
  if (k.private) parts.push("private workflows too");
  return parts.join("; ");
}

function KeyCard({ k, list, screenName, onList, onSecret }: { k: ApiKey; list: KeyList; screenName: (id: string) => string; onList: (l: KeyList) => void; onSecret: (s: string) => void }) {
  const [mode, setMode] = useState<"view" | "edit" | "roll" | "revoke">("view");
  const [grace, setGrace] = useState("24");
  const [busy, setBusy] = useState(false);
  const act = async (f: () => Promise<void>) => {
    setBusy(true);
    try {
      await f();
      setMode("view");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "That didn't work");
    } finally {
      setBusy(false);
    }
  };

  if (mode === "edit") {
    return (
      <li>
        <KeyForm
          list={list}
          initial={k}
          onCancel={() => setMode("view")}
          onSave={async (fields) => {
            onList(await changeKey(k.id, fields));
            setMode("view");
          }}
        />
      </li>
    );
  }
  const limits = [k.per_day ? `${k.per_day} pictures a day` : "no daily limit", k.concurrent ? `${k.concurrent} at once` : "any number at once"].join(" · ");
  return (
    <li className="space-y-2 rounded-2xl border border-stone-200/80 bg-card px-3 py-2" data-testid={`api-key-${k.id}`}>
      <div className="min-w-0">
        <p className="truncate text-base font-medium leading-snug">{k.name}</p>
        <p className="truncate font-mono text-sm text-muted-foreground">{k.hint}</p>
      </div>
      <p className="text-[15px] leading-snug">{what(k, screenName)}</p>
      <p className="text-sm leading-snug text-muted-foreground">
        {limits}
        <br />
        {k.last_used_at ? `Last used ${ago(k.last_used_at)}${k.last_used_ip ? ` from ${k.last_used_ip}` : ""}` : "Not used yet"}
        {k.last_24h ? ` · ${k.last_24h} pictures in the last 24 h` : ""}
        <br />
        {k.expires_at ? `Runs out ${day(k.expires_at)}` : "Never runs out"}
        {k.prev_until && k.prev_until > Date.now() / 1000 ? ` · the old secret works until ${new Date(k.prev_until * 1000).toLocaleString()}` : ""}
      </p>

      {mode === "view" && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setMode("edit")}>Change</Button>
          <Button onClick={() => setMode("roll")}>New secret</Button>
          <Button onClick={() => setMode("revoke")} testId={`api-revoke-${k.id}`}>
            Revoke
          </Button>
        </div>
      )}
      {mode === "roll" && (
        <div className="space-y-2">
          <Chips
            label="The old secret"
            value={grace}
            options={[
              { value: "24", label: "Old one works 24 h more" },
              { value: "0", label: "Old one stops now" },
            ]}
            onChange={setGrace}
          />
          <div className="flex gap-2">
            <Button tone="primary" disabled={busy} onClick={() => void act(async () => {
              const r = await rollKey(k.id, Number(grace));
              onList(r);
              onSecret(r.secret);
            })}>
              Make a new secret
            </Button>
            <Button onClick={() => setMode("view")}>Cancel</Button>
          </div>
        </div>
      )}
      {mode === "revoke" && (
        <div className="space-y-2">
          <p className="text-[15px]">Revoke {k.name}? Anything using it stops working at once, and it can't be undone.</p>
          <div className="flex gap-2">
            <Button tone="danger" disabled={busy} onClick={() => void act(async () => onList(await revokeKey(k.id)))} testId={`api-revoke-yes-${k.id}`}>
              Revoke
            </Button>
            <Button onClick={() => setMode("view")}>Keep it</Button>
          </div>
        </div>
      )}
    </li>
  );
}

function KeyForm({ list, initial, onSave, onCancel }: { list: KeyList | null; initial?: ApiKey; onSave: (f: KeyFields) => Promise<void>; onCancel: () => void }) {
  const [name, setName] = useState(initial?.name ?? "");
  const [show, setShow] = useState(initial?.scopes.includes("publish") ? "yes" : "no");
  const [screens, setScreens] = useState<string[]>(initial?.destinations ?? []);
  const [priv, setPriv] = useState(initial?.private ? "yes" : "no");
  const [perDay, setPerDay] = useState(String(initial?.per_day ?? 100));
  const [atOnce, setAtOnce] = useState(String(initial?.concurrent ?? 3));
  const [expires, setExpires] = useState("365");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const fields: KeyFields = {
      name: name.trim(),
      scopes: show === "yes" ? ["generate", "publish"] : ["generate"],
      destinations: show === "yes" ? screens : [],
      private: priv === "yes",
      per_day: Number(perDay),
      concurrent: Number(atOnce),
    };
    if (!initial) fields.expires_days = expires === "never" ? null : Number(expires);
    setBusy(true);
    try {
      await onSave(fields);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the key");
    } finally {
      setBusy(false);
    }
  };

  const options = (opts: { value: string; label: string }[], current: string) =>
    opts.some((o) => o.value === current) ? opts : [...opts, { value: current, label: current }];

  return (
    <div className="space-y-4 rounded-2xl border border-stone-200/80 bg-card p-3" data-testid="api-key-form">
      <Sub className="pt-0">{initial ? `Change ${initial.name}` : "A new key"}</Sub>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="What it's for (e.g. My agent)"
        aria-label="What the key is for"
        maxLength={80}
        className="min-h-11 w-full rounded-xl border border-stone-300 bg-white px-3 text-[15px]"
        data-testid="api-key-name"
      />
      <Row label="Show pictures on screens" hint="Off: it only makes pictures, into Generated" stack>
        <Chips label="Show pictures on screens" value={show} options={[{ value: "no", label: "No" }, { value: "yes", label: "Yes, on…" }]} onChange={setShow} />
      </Row>
      {show === "yes" && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="The screens">
          {(list?.destinations ?? []).map((d) => {
            const on = screens.includes(d.id);
            return (
              <button
                key={d.id}
                type="button"
                aria-pressed={on}
                onClick={() => setScreens(on ? screens.filter((s) => s !== d.id) : [...screens, d.id])}
                className={cn("inline-flex min-h-11 items-center rounded-full border px-4 text-[15px] font-medium", on ? "border-teal-700 bg-teal-700 text-white" : "border-stone-200 bg-white text-stone-700")}
              >
                {d.name}
              </button>
            );
          })}
        </div>
      )}
      <Row label="Private workflows" hint="The deployment's own private catalogue" stack>
        <Chips label="Private workflows" value={priv} options={[{ value: "no", label: "No" }, { value: "yes", label: "Yes" }]} onChange={setPriv} />
      </Row>
      <Row label="Pictures a day" stack>
        <Chips label="Pictures a day" value={perDay} options={options(PER_DAY, perDay)} onChange={setPerDay} />
      </Row>
      <Row label="At once" stack>
        <Chips label="Generations at once" value={atOnce} options={options(AT_ONCE, atOnce)} onChange={setAtOnce} />
      </Row>
      {!initial && (
        <Row label="Runs out after" stack>
          <Chips label="Runs out after" value={expires} options={EXPIRES} onChange={setExpires} />
        </Row>
      )}
      <div className="flex gap-2">
        <Button tone="primary" disabled={busy || !name.trim() || (show === "yes" && screens.length === 0)} onClick={() => void save()} testId="api-key-save">
          {initial ? "Save" : "Make the key"}
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
