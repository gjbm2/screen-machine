// Asked when the person publishes a picture of another shape than the screen (owner, 19 Nov: offer crop, frame, adapt
// and auto, auto the default). One tap chooses; mounted once in App.tsx and reached through askFit (shared/fitChoice).
import { useEffect, useRef, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { setFitAsker, shapeWord, type Fit, type FitQuestion } from "@/shared/fitChoice";

const CHOICES: { fit: Fit; label: string; words: string }[] = [
  { fit: "auto", label: "Auto", words: "AI looks at the picture and fits it: trims a little where nothing is lost, paints in the rest." },
  { fit: "crop", label: "Crop", words: "Fills the screen with the most important part." },
  { fit: "frame", label: "Frame", words: "Shows the whole picture, with black bars." },
  { fit: "adapt", label: "Adapt", words: "Keeps the whole picture and paints more of the scene around it." },
];

export function FitChoiceDialog() {
  const [question, setQuestion] = useState<FitQuestion | null>(null);
  const answer = useRef<((fit: Fit | null) => void) | null>(null);

  useEffect(() => {
    setFitAsker(
      (q) =>
        new Promise<Fit | null>((resolve) => {
          answer.current?.(null);               // a question still open is cancelled by a new one
          answer.current = resolve;
          setQuestion(q);
        }),
    );
    return () => setFitAsker(null);
  }, []);

  const close = (fit: Fit | null) => {
    answer.current?.(fit);
    answer.current = null;
    setQuestion(null);
  };

  return (
    <Dialog open={!!question} onOpenChange={(open) => !open && close(null)}>
      <DialogContent className="max-w-md" data-testid="fit-choice">
        {question && (
          <>
            <DialogHeader>
              <DialogTitle className="pr-6">
                This picture is {shapeWord(question.picture)}; {question.name} is {shapeWord(question.size)}
              </DialogTitle>
              <DialogDescription>How should it fill the screen?</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              {CHOICES.map((c, i) => (
                <button
                  key={c.fit}
                  type="button"
                  autoFocus={i === 0}
                  onClick={() => close(c.fit)}
                  data-testid={`fit-${c.fit}`}
                  className={
                    "w-full rounded-xl border px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                    (i === 0 ? "border-teal-700 bg-teal-50 hover:bg-teal-100" : "border-stone-200 bg-white hover:bg-stone-50")
                  }
                >
                  <span className="block text-[15px] font-semibold text-stone-900">
                    {c.label}
                    {i === 0 && <span className="ml-2 text-xs font-medium text-teal-700">Recommended</span>}
                  </span>
                  <span className="block text-sm leading-snug text-stone-600">{c.words}</span>
                </button>
              ))}
              <p className="pt-1 text-xs text-muted-foreground">Auto and Adapt take about a minute; the screen changes when the picture is ready.</p>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
