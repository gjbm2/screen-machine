// Plug-ins: counts, rev and rescan age; when open the per-kind table, import errors, runtime errors in words and
// preset problems; Rescan (POST /dev/lab/rescan) and Reload (refetch the Library index).  Roots and collisions show
// only if the server returns them.
import { h, put, http, ago } from "../kit.js";
import { S } from "../store.js";
import { lib, load as loadLibrary, loadLab, pluginTitle } from "../data/library.js";
import { button, busy } from "../ui/controls.js";
import { toast, toastErr } from "../ui/toast.js";
import { errorWords, friendly, pluginErrorWords } from "../words.js";

export function mountPlugins(host, ctx) {
  const rescan = button("Rescan", { kind: "primary", tid: "rescan-btn", icon: "refresh" });
  const reload = button("Reload the Library", { kind: "outline", tid: "reload-btn" });
  const body = h("div", { class: "stack" });
  put(host, h("div", { class: "stack" }, body, h("div", { class: "row" }, rescan, reload)));
  rescan.addEventListener("click", async () => {
    busy(rescan, true, "Rescanning…");
    const r = await http("POST", "/dev/lab/rescan", {});
    busy(rescan, false);
    if (!r.ok) { toastErr(errorWords(r, { what: "Rescan" })); return; }
    const d = r.data || {};
    toast(`Rescanned in ${d.took_ms ?? "?"} ms: ${d.reloaded?.length || 0} reloaded, ${d.added?.length || 0} added, ${d.removed?.length || 0} removed${d.errors?.length ? `, ${d.errors.length} with errors` : ""}`, { tone: d.errors?.length ? "warn" : "" });
    await Promise.all([loadLibrary("rescan"), loadLab()]);
  });
  reload.addEventListener("click", async () => { busy(reload, true, "Reloading…"); await Promise.all([loadLibrary("reload"), loadLab()]); busy(reload, false); toast(`The Library has ${lib.items.length} items`); });

  function draw() {
    const L = lib.lab;
    if (!L) { put(body, h("p", { class: "hint" }, lib.labErr ? `No plug-in status: ${errorWords(lib.labErr)}` : "Loading…")); ctx.sec.plugins.setHint(""); return; }
    const plugins = L.plugins || [];
    const errs = plugins.filter((p) => p.load === "error" || p.load === "stale");
    const rt = plugins.filter((p) => p.runtime_errors?.count);
    ctx.sec.plugins.setHint(`${plugins.length} · rev ${L.rev ?? "–"}${errs.length ? ` · ${errs.length} broken` : ""}${rt.length ? ` · ${rt.length} with errors` : ""}`);
    const byKind = {};
    for (const p of plugins) { const k = (byKind[p.kind] ||= { ok: 0, error: 0, stale: 0, shadowed: 0 }); if (p.shadowed_by) k.shadowed++; else k[p.load] = (k[p.load] || 0) + 1; }
    put(body,
      h("p", { class: "muted" }, `${plugins.length} plug-ins · rev ${L.rev ?? "–"} · rescanned ${L.rescanned_at ? ago(L.rescanned_at) : "–"} (every ${L.rescan_s ?? 20} s)`),
      h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" }, h("thead", {}, h("tr", {}, ["Kind", "Working", "Broken", "Stale", "Shadowed"].map((x) => h("th", {}, x)))),
        h("tbody", {}, Object.entries(byKind).map(([k, v]) => h("tr", {}, [friendly(k === "source" ? "scene" : k), v.ok, v.error, v.stale, v.shadowed].map((x) => h("td", {}, String(x ?? 0)))))))),
      errs.length ? h("div", {}, h("h3", { class: "group-h" }, "Broken (import errors)"), h("ul", {}, errs.map((p) => h("li", {}, h("strong", {}, p.name), `: ${String(p.error || p.load).split("\n")[0]}`)))) : null,
      rt.length ? h("details", { class: "unroll", open: rt.length <= 5 ? true : null }, h("summary", {}, `Runtime errors (${rt.length})`, h("span", { class: "hint" }, "too slow for this GPU, or failed while running")),
        h("ul", {}, rt.map((p) => h("li", {}, h("strong", {}, pluginTitle(p.name) || friendly(p.name)), `: ${pluginErrorWords({ detail: p.runtime_errors.last_detail })} (×${p.runtime_errors.count})`)))) : null,
      L.preset_problems?.length ? h("details", { class: "unroll" }, h("summary", {}, `${L.preset_problems.length} preset problems`), h("ul", { class: "meta" }, L.preset_problems.map((p) => h("li", {}, String(p))))) : null,
      L.roots?.length ? h("div", {}, h("h3", { class: "group-h" }, "Plug-in folders"), L.roots.map((r) => h("div", { class: "meta" }, `${r.path} · ${r.origin}${r.errors?.length ? " · " + r.errors.join("; ") : ""}`))) : null,
      L.collisions?.length ? h("div", {}, h("h3", { class: "group-h" }, "Names shadowed by core"), h("p", { class: "meta" }, L.collisions.map((c) => `${c.kind} ${c.name} (kept ${c.kept})`).join(" · "))) : null);
  }
  S.on(["labVersion"], draw);
  ctx.sec.plugins.addEventListener("toggle", () => { if (ctx.sec.plugins.open) loadLab(); });
  draw();
}
