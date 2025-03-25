
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
    
    // IMPORTANT FIX: Ensure the old image is displayed on top initially with z-index 10
    // The new image should be behind it with z-index 5
    setOldImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      transition: `opacity ${duration}s ease-in-out`,
      opacity: 1,
      zIndex: 10, // Higher z-index to be visible initially
      ...getPositionStyle(position, showMode)
    });
    
    // Set up the new image to be invisible initially but ready to fade in
    setNewImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      opacity: 0,
      zIndex: 5, // Lower z-index initially
      ...getPositionStyle(position, showMode)
    });
    
    setIsTransitioning(true);
    console.log('[useTransitionEffect] Transition initialized, isTransitioning =', true);
    
    return duration;
  };

  const executeTransition = (duration: number, onComplete: () => void) => {
    console.log('[useTransitionEffect] Executing transition with duration:', duration);
    
    // Small delay to ensure DOM updates before starting transition
    setTimeout(() => {
      console.log('[useTransitionEffect] Starting fade transition animation');
      
      // IMPORTANT FIX: When fading, change the z-index of the new image to be above the old one
      // First make sure the new image is ready with transition property
      setNewImageStyle(prev => ({
        ...prev,
        transition: `opacity ${duration}s ease-in-out`,
        zIndex: 10 // Move new image to front during transition
      }));
      
      // Small additional delay to ensure the transition property is applied
      setTimeout(() => {
        // Now fade out the old image
        setOldImageStyle(prev => ({
          ...prev,
          opacity: 0,
          zIndex: 5 // Move old image to back during transition
        }));
        
        // And fade in the new image
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
      }, 50);
    }, 50);
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
