
import { useState } from 'react';
import { DisplayParams, TransitionType } from '../types';

export const useTransitionEffect = () => {
  const [isTransitioning, setIsTransitioning] = useState<boolean>(false);
  const [oldImageUrl, setOldImageUrl] = useState<string | null>(null);
  const [oldImageStyle, setOldImageStyle] = useState<React.CSSProperties>({});
  const [newImageStyle, setNewImageStyle] = useState<React.CSSProperties>({});

  const initializeTransition = (
    currentImage: string | null,
    position: string,
    showMode: string,
    getPositionStyle: (position: string, showMode: string) => React.CSSProperties,
    transitionType: string = 'fade-fast'
  ) => {
    console.log('[useTransitionEffect] Initializing transition with type:', transitionType, 'current image:', currentImage);
    
    if (!currentImage) {
      console.log('[useTransitionEffect] No current image, skipping transition setup');
      return 0;
    }
    
    setOldImageUrl(currentImage);
    
    // Determine transition duration based on type
    let duration = 2; // default for fade-fast
    
    if (transitionType === 'fade-slow') {
      duration = 10;
      console.log('[useTransitionEffect] Slow fade transition: duration =', duration);
    } else if (transitionType === 'fade-fast') {
      duration = 2;
      console.log('[useTransitionEffect] Fast fade transition: duration =', duration);
    }
    
    // Set up the old image to be visible initially
    setOldImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      transition: `opacity ${duration}s ease-in-out`,
      opacity: 1,
      zIndex: 1,
      ...getPositionStyle(position, showMode)
    });
    
    // Set up the new image to be invisible initially - CRITICAL FIX: add transition property here
    setNewImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      transition: `opacity ${duration}s ease-in-out`, // Add transition property from the start
      opacity: 0,
      zIndex: 2,
      ...getPositionStyle(position, showMode)
    });
    
    setIsTransitioning(true);
    console.log('[useTransitionEffect] Transition initialized, isTransitioning =', true);
    
    return duration;
  };

  const executeTransition = (duration: number, onComplete: () => void) => {
    console.log('[useTransitionEffect] Executing transition with duration:', duration);
    
    // First ensure the DOM is updated with initial styles
    requestAnimationFrame(() => {
      // Then wait for one more frame to ensure styles are applied
      requestAnimationFrame(() => {
        console.log('[useTransitionEffect] Starting fade transition animation');
        
        // Fade out the old image
        setOldImageStyle(prev => ({
          ...prev,
          opacity: 0
        }));
        
        // Fade in the new image
        setNewImageStyle(prev => ({
          ...prev,
          opacity: 1
        }));
        
        // After the transition duration, clean up and notify completion
        setTimeout(() => {
          console.log('[useTransitionEffect] Fade transition complete');
          setIsTransitioning(false);
          setOldImageUrl(null);
          
          // Execute the completion callback
          onComplete();
        }, duration * 1000);
      });
    });
  };

  return {
    isTransitioning,
    oldImageUrl,
    oldImageStyle,
    newImageStyle,
    initializeTransition,
    executeTransition
  };
};
