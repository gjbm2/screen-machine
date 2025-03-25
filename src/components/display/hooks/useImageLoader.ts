
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

      console.log('[useImageLoader] Loading new image with transition type:', params.transition);

      // Direct cut transition or no current image
      if (params.transition === 'cut' || !currentImageUrl) {
        console.log('[useImageLoader] Loading new image with cut transition:', url);
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        setImageChanged(false);
        
        // Extract metadata immediately for cut transitions
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
        // Fade transition - slow or fast
        console.log('[useImageLoader] Loading new image with fade transition:', url, params.transition);
        setIsLoading(true);
        
        // Preload the new image
        const preloadImg = new Image();
        preloadImg.crossOrigin = "anonymous";
        
        preloadImg.onload = async () => {
          console.log('[useImageLoader] Image preloaded successfully, preparing transition');
          
          // Get metadata first, but don't update the caption yet
          let newMetadata = {};
          if (params.data !== undefined || params.caption) {
            try {
              console.log('[useImageLoader] Extracting metadata for new image (fade transition)');
              newMetadata = await extractMetadataFromImage(url, params.data || undefined);
            } catch (err) {
              console.error('[useImageLoader] Error extracting metadata:', err);
            }
          }
          
          // Initialize the transition with the current image
          console.log('[useImageLoader] Initializing transition with type:', params.transition);
          const duration = initializeTransition(
            currentImageUrl, 
            params.position, 
            params.showMode,
            getImagePositionStyle,
            params.transition
          );
          
          console.log('[useImageLoader] Transition duration set to:', duration, 'seconds');
          
          // Set the new image URL and increment key
          setImageUrl(url);
          setImageKey(prev => prev + 1);
          
          // Execute the transition after a short delay to ensure new image is loaded into the DOM
          setTimeout(() => {
            console.log('[useImageLoader] Executing fade transition');
            executeTransition(duration, () => {
              // This callback runs after transition is complete
              console.log('[useImageLoader] Transition complete');
              setImageChanged(false);
              
              // Only update caption after transition is complete
              if (params.caption && Object.keys(newMetadata).length > 0) {
                console.log('[useImageLoader] Updating caption after transition complete');
                updateCaption(params.caption, newMetadata);
              }
            });
          }, 50);
          
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
        
        // Initiate preloading
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
