
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
    
    // Important: Set initial styles without transitions first
    // Set up the old image to be visible initially
    setOldImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      opacity: 1,
      zIndex: 1,
      ...getPositionStyle(position, showMode)
    });
    
    // Set up the new image to be invisible initially
    setNewImageStyle({
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
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
    
    // Give browser time to paint the initial state
    requestAnimationFrame(() => {
      // Then add transition property to both elements
      setOldImageStyle(prev => ({
        ...prev,
        transition: `opacity ${duration}s ease-in-out`
      }));
      
      setNewImageStyle(prev => ({
        ...prev,
        transition: `opacity ${duration}s ease-in-out`
      }));
      
      // Wait for transition properties to be applied
      requestAnimationFrame(() => {
        // Force a reflow to ensure transitions are applied
        document.body.offsetHeight;
        
        console.log('[useTransitionEffect] Starting fade transition animation');
        
        // Now change opacity to trigger the transition
        setOldImageStyle(prev => ({
          ...prev,
          opacity: 0
        }));
        
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
