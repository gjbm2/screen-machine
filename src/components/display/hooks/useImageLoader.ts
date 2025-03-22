
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
    getPositionStyle: (position: string, showMode: string) => React.CSSProperties
  ) => number,
  executeTransition: (duration: number, onComplete: () => void) => void,
  getImagePositionStyle: (position: string, showMode: string) => React.CSSProperties
) => {
  const loadNewImage = async (
    url: string,
    currentImageUrl: string | null,
    params: DisplayParams
  ) => {
    // Direct cut transition or no current image
    if (params.transition === 'cut' || !currentImageUrl) {
      setImageUrl(url);
      setImageKey(prev => prev + 1);
      setImageChanged(false);
      
      // Extract metadata
      try {
        const newMetadata = await extractMetadataFromImage(url, params.data || undefined);
        
        // Process caption with new metadata if caption exists
        if (params.caption) {
          updateCaption(params.caption, newMetadata);
        }
      } catch (err) {
        console.error('Error extracting metadata:', err);
      }
    } else {
      // Fade transition
      setIsLoading(true);
      
      // Preload the new image
      const preloadImg = new Image();
      preloadImg.onload = async () => {
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        
        // Extract metadata for the new image
        try {
          const newMetadata = await extractMetadataFromImage(url, params.data || undefined);
          
          // Process caption with new metadata if caption exists
          if (params.caption) {
            updateCaption(params.caption, newMetadata);
          }
        } catch (err) {
          console.error('Error extracting metadata:', err);
        }
        
        const duration = params.transition === 'fade-fast' ? 1 : 2;
        
        // Initialize the transition
        initializeTransition(
          currentImageUrl, 
          params.position, 
          params.showMode,
          getImagePositionStyle
        );
        
        // Execute the transition
        executeTransition(duration, () => {
          setImageChanged(false);
        });
        
        setIsLoading(false);
      };
      
      preloadImg.onerror = () => {
        setImageUrl(url);
        setImageKey(prev => prev + 1);
        setIsLoading(false);
        setImageChanged(false);
        toast.error("Failed to preload image for transition");
      };
      
      preloadImg.src = url;
    }
  };

  return {
    loadNewImage
  };
};
