
import { useState } from 'react';
import { DisplayParams } from '../types';

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
    setOldImageUrl(currentImage);
    
    // Determine transition duration based on type
    let duration = 2; // default for fade-fast
    
    if (transitionType === 'fade-slow') {
      duration = 10;
    } else if (transitionType === 'fade-fast') {
      duration = 2;
    }
    
    setOldImageStyle({
      position: 'absolute',
      transition: `opacity ${duration}s ease`,
      opacity: 1,
      zIndex: 2,
      ...getPositionStyle(position, showMode)
    });
    
    setNewImageStyle({
      ...getPositionStyle(position, showMode),
      opacity: 0,
      zIndex: 1
    });
    
    setIsTransitioning(true);
    
    return duration;
  };

  const executeTransition = (duration: number, onComplete: () => void) => {
    setTimeout(() => {
      setOldImageStyle(prev => ({
        ...prev,
        opacity: 0
      }));
      
      setNewImageStyle(prev => ({
        ...prev,
        opacity: 1,
        transition: `opacity ${duration}s ease`
      }));
      
      setTimeout(() => {
        setIsTransitioning(false);
        setOldImageUrl(null);
        onComplete();
      }, duration * 1000);
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
