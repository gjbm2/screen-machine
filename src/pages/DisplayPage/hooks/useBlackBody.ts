// While the display page is mounted, <html> and <body> are black, so a crash, a reload or a gap
// between layers shows black rather than the app's light theme (LIVE_V1 §2.2 rule 10).
import { useLayoutEffect } from "react";

export function useBlackBody(): void {
  useLayoutEffect(() => {
    const html = document.documentElement.style;
    const body = document.body.style;
    const prev = { html: html.backgroundColor, body: body.backgroundColor };
    html.backgroundColor = "black";
    body.backgroundColor = "black";
    return () => {
      html.backgroundColor = prev.html;
      body.backgroundColor = prev.body;
    };
  }, []);
}
