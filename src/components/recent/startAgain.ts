// Start again: a picture's prompt and its reference pictures back in the generator (Generated's panels and tiles).
import { toast } from 'sonner';
import { ReferenceImageService } from '@/services/reference-image-service';
import type { ImageItem } from '@/types/image-types';

export function startAgain(img: ImageItem): void {
  try {
    window.dispatchEvent(new CustomEvent('setPromptText', { detail: { prompt: img.promptKey || img.metadata?.prompt || '' } }));
    const urls = ReferenceImageService.getReferenceImageUrls(img.bucketId || '_recent', img.reference_images || []);
    // the first replaces what the prompt had, the rest are added to it
    urls.forEach((url, i) => window.dispatchEvent(new CustomEvent('useImageAsPrompt', { detail: { url, append: i > 0 } })));
    toast.success('Loaded prompt and reference images for editing');
  } catch (error) {
    console.error('Error starting again:', error);
    toast.error('Failed to load prompt and reference images');
  }
}
