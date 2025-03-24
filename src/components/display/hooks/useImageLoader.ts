
import { toast } from 'sonner';
import { DisplayParams, TransitionType } from '../types';

export const useImageLoader = (
  setIsLoading: (isLoading: boolean) => void,
  setImageUrl: (url: string | null) => void,
  setImageKey: (fn: (prev: number) => number) => void,
  setImageChanged: (changed: boolean) => void,
  extractMetadataFromImage: (url: string, dataTag?: string) => Promise<Record<string, string>>,
  updateCaption: (caption: string | null, metadata: Record<string, string>) => void,
  initializeTransition: (
    currentImage: string | null, 
    position: string, 
    showMode: string,
    getPositionStyle: (position: string, showMode: string) => React.CSSProperties,
    transitionType?: string
  ) => number,
  executeTransition: (duration: number, onComplete: () => void) => void,
  getImagePositionStyle: (position: string, showMode: string) => React.CSSProperties
) => {
  const loadNewImage = async (
    url: string,
    currentImageUrl: string | null,
    params: DisplayParams
  ) => {
    try {
      // Skip if the URL is the same as the current one and hasn't changed
      if (url === currentImageUrl) {
        console.log('[useImageLoader] Image URL unchanged, skipping load');
        return;
      }

      // Direct cut transition or no current image
      if (params.transition === 'cut' || !currentImageUrl) {
        console.log('[useImageLoader] Loading new image with cut transition:', url);
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        setImageChanged(false);
        
        // Extract metadata only if needed
        if (params.data !== undefined || params.caption) {
          try {
            console.log('[useImageLoader] Extracting metadata for new image (cut transition)');
            const newMetadata = await extractMetadataFromImage(url, params.data || undefined);
            
            // Process caption with new metadata if caption exists
            if (params.caption) {
              updateCaption(params.caption, newMetadata);
            }
          } catch (err) {
            console.error('[useImageLoader] Error extracting metadata:', err);
          }
        }
      } else {
        // Fade transition
        console.log('[useImageLoader] Loading new image with fade transition:', url);
        setIsLoading(true);
        
        // Preload the new image
        const preloadImg = new Image();
        preloadImg.onload = async () => {
          setImageUrl(url);
          setImageKey(prev => prev + 1);
          
          // Extract metadata for the new image only if needed
          if (params.data !== undefined || params.caption) {
            try {
              console.log('[useImageLoader] Extracting metadata for new image (fade transition)');
              const newMetadata = await extractMetadataFromImage(url, params.data || undefined);
              
              // Process caption with new metadata if caption exists
              if (params.caption) {
                updateCaption(params.caption, newMetadata);
              }
            } catch (err) {
              console.error('[useImageLoader] Error extracting metadata:', err);
            }
          }
          
          // Initialize the transition, passing the transition type
          const duration = initializeTransition(
            currentImageUrl, 
            params.position, 
            params.showMode,
            getImagePositionStyle,
            params.transition
          );
          
          // Execute the transition
          executeTransition(duration, () => {
            setImageChanged(false);
          });
          
          setIsLoading(false);
        };
        
        preloadImg.onerror = (error) => {
          console.error('[useImageLoader] Failed to preload image:', error);
          setImageUrl(url); // Still set the URL so user can see the error
          setImageKey(prev => prev + 1);
          setIsLoading(false);
          setImageChanged(false);
          toast.error("Failed to preload image for transition");
        };
        
        preloadImg.src = url;
      }
    } catch (error) {
      console.error('[useImageLoader] Unexpected error loading image:', error);
      toast.error("Error loading image");
      setIsLoading(false);
    }
  };

  return {
    loadNewImage
  };
};
