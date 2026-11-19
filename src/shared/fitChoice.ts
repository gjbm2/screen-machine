// How a picture of another shape than its screen is fitted to it when the person publishes it (owner, 19 Nov: offer
// crop, frame, adapt, auto; auto the default). routes/reframe.py does the fitting; FitChoiceDialog (mounted once in
// App.tsx) asks; publishImageUnified asks through askFit before it publishes a picture POST /api/publish/fit-check says
// is the wrong shape.

export type Fit = "auto" | "crop" | "frame" | "adapt";

export interface FitQuestion {
  screen: string;
  name: string;
  picture: [number, number];
  size: [number, number];
}

type Asker = (q: FitQuestion) => Promise<Fit | null>;
let asker: Asker | null = null;

/** The mounted dialog registers itself here (null when it unmounts). */
export function setFitAsker(fn: Asker | null): void {
  asker = fn;
}

/** The person's choice; null when they cancel; undefined when no dialog is mounted (the screen's own setting then). */
export function askFit(q: FitQuestion): Promise<Fit | null | undefined> {
  return asker ? asker(q) : Promise.resolve(undefined);
}

/** "portrait", "landscape" or "square", for the dialog's words. */
export function shapeWord([w, h]: [number, number]): string {
  if (Math.abs(w / h - 1) < 0.05) return "square";
  return w > h ? "landscape" : "portrait";
}
