// One link for the whole app: a same-origin path ("/room", "/music?tab=mine", "/settings#look") is a react-router
// <Link>, so a tap stays inside the app (no reload: the socket, the docked player and the page state survive, which
// matters most in the iPad Home Screen app). Anything else (another origin, the renderer's pages behind /renderer or
// /show on room_service, a JSON route, a new tab) is a plain <a>. A link with #<fold> also opens that fold when the
// page is already showing (Section.tsx only reads the hash when it mounts).

import { forwardRef, type AnchorHTMLAttributes, type MouseEvent } from "react";
import { Link } from "react-router-dom";
import { openFold } from "@/pages/Room/Section";

/** Paths served by the app's own router (everything else on this origin is Flask's or a pass-through). */
const NOT_APP = /^\/(api|rs|ws|renderer|show|test-buckets|simulate-scheduler|static|output|assets)(\/|$|\?|#)/;

export function isAppPath(href: string): boolean {
  if (!href.startsWith("/") || href.startsWith("//")) {
    // an absolute link to this same origin counts too
    try {
      const u = new URL(href, window.location.href);
      if (u.origin !== window.location.origin) return false;
      return isAppPath(`${u.pathname}${u.search}${u.hash}`);
    } catch {
      return false;
    }
  }
  return !NOT_APP.test(href);
}

function local(href: string): string {
  if (href.startsWith("/") && !href.startsWith("//")) return href;
  const u = new URL(href, window.location.href);
  return `${u.pathname}${u.search}${u.hash}`;
}

export type AppLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  href: string;
  /** Opens in a new tab (always a plain <a>). */
  newTab?: boolean;
  /** react-router state for the next page (e.g. {from: "Room"} so its back link knows where it came from). */
  state?: unknown;
};

export const AppLink = forwardRef<HTMLAnchorElement, AppLinkProps>(function AppLink({ href, newTab, state, onClick, ...rest }, ref) {
  const hash = href.includes("#") ? href.slice(href.indexOf("#") + 1) : "";
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (hash && !e.defaultPrevented) requestAnimationFrame(() => openFold(decodeURIComponent(hash)));
  };
  if (newTab) return <a ref={ref} href={href} target="_blank" rel="noopener noreferrer" onClick={onClick} {...rest} />;
  if (isAppPath(href)) return <Link ref={ref} to={local(href)} state={state} onClick={click} {...rest} />;
  return <a ref={ref} href={href} onClick={onClick} {...rest} />;
});
