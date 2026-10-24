// Any address the app doesn't have: the warm skin, plain words and a way into the three main places (Room, Music,
// Generator), so a mistyped link or an old bookmark never strands anyone (the iPad Home Screen app has no address bar).

import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { AppLink } from "@/components/nav/AppLink";
import { generatorUrl, musicPageUrl, roomPageUrl } from "@/components/nav/appNav";
import { cn } from "@/lib/utils";
import { CARD_CLASS, RoomThemeStyle, TAP_BUTTON_CLASS, THEME_CLASS } from "@/pages/Room/theme";

const NotFound = () => {
  const location = useLocation();

  useEffect(() => {
    console.error("404 Error: User attempted to access non-existent route:", location.pathname);
  }, [location.pathname]);

  useEffect(() => {
    const prev = document.title;
    document.title = "Not found · screen/machine";
    return () => {
      document.title = prev;
    };
  }, []);

  const places = [
    { label: "Room", href: roomPageUrl(), primary: true },
    { label: "Music", href: musicPageUrl(), primary: false },
    { label: "Generator", href: generatorUrl(), primary: false },
  ];

  return (
    <div
      className={cn(THEME_CLASS, "flex min-h-screen items-center justify-center bg-background px-4 text-foreground antialiased")}
    >
      <RoomThemeStyle />
      <main className={cn(CARD_CLASS, "w-full max-w-sm p-6 text-center")} data-testid="not-found">
        <h1 className="text-xl font-semibold">That page doesn't exist</h1>
        <p className="mt-2 break-words text-sm text-muted-foreground">
          There's nothing at <span className="font-mono">{location.pathname}</span>. Pick where to go:
        </p>
        <nav aria-label="Go to" className="mt-5 flex flex-col gap-2">
          {places.map((p) => (
            <AppLink
              key={p.label}
              href={p.href}
              className={cn(
                TAP_BUTTON_CLASS,
                "inline-flex items-center justify-center font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                p.primary
                  ? "bg-primary text-primary-foreground hover:bg-primary/90"
                  : "border border-border bg-card hover:bg-secondary",
              )}
            >
              {p.label}
            </AppLink>
          ))}
        </nav>
      </main>
    </div>
  );
};

export default NotFound;
