// Start again (owner, 24 Nov): a picture or clip's recipe (recipe.ts) back in the generator. A still: Image, with the
// words as written, its pictures, workflow, settings and prompt helper. A film segment: Film, with its pictures in the
// slots and its words and seconds on the link; from its batch, the whole film. The form does the setting up
// (PromptForm, START_AGAIN_EVENT).
import { toast } from 'sonner';
import type { ImageItem } from '@/types/image-types';
import { recipeOf } from './recipe';

export const START_AGAIN_EVENT = 'generator:start-again';

/** `batch`: the pictures made with it, where known (a film is rebuilt from all its segments). */
export function startAgain(img: ImageItem, batch: ImageItem[] = []): void {
  try {
    const recipe = recipeOf(img, batch);
    window.dispatchEvent(new CustomEvent(START_AGAIN_EVENT, { detail: recipe }));
    toast.success(recipe.kind === 'film' ? 'The film is back in Film' : 'Ready to make again');
  } catch (error) {
    console.error('Error starting again:', error);
    toast.error("Couldn't start again from that");
  }
}
