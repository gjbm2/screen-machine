
import { DisplayParams } from '../types';

export const useImageStyler = () => {
  const getImagePositionStyle = (position: string, showMode: string, containerWidth?: number, containerHeight?: number, imageWidth?: number, imageHeight?: number): React.CSSProperties => {
    // Base position styles (absolute positioning for all modes)
    const positionStyle: React.CSSProperties = { position: 'absolute' };
    
    // Base style depending on display mode
    let baseStyle: React.CSSProperties = {};
    
    switch (showMode) {
      case 'fill':
        baseStyle = {
          width: '100%',
          height: '100%',
          objectFit: 'cover',
        };
        break;
        
      case 'fit':
        baseStyle = {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
        break;
        
      case 'stretch':
        baseStyle = {
          width: '100%',
          height: '100%',
          objectFit: 'fill',
        };
        break;
        
      case 'actual':
        // For actual size, we need container and image dimensions
        if (containerWidth && containerHeight && imageWidth && imageHeight) {
          // No scaling, just use original dimensions
          baseStyle = {
            width: `${imageWidth}px`,
            height: `${imageHeight}px`,
            objectFit: 'none',
          };
        } else {
          // Fallback if dimensions are not available
          baseStyle = {
            width: 'auto',
            height: 'auto',
            objectFit: 'none',
          };
        }
        break;
        
      default:
        baseStyle = {
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
        };
    }
    
    // Apply position
    switch (position) {
      case 'top-left':
        return { ...baseStyle, ...positionStyle, top: 0, left: 0 };
      case 'top-center':
        return { ...baseStyle, ...positionStyle, top: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'top-right':
        return { ...baseStyle, ...positionStyle, top: 0, right: 0 };
      case 'center-left':
        return { ...baseStyle, ...positionStyle, top: '50%', left: 0, transform: 'translateY(-50%)' };
      case 'center':
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
      case 'center-right':
        return { ...baseStyle, ...positionStyle, top: '50%', right: 0, transform: 'translateY(-50%)' };
      case 'bottom-left':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: 0 };
      case 'bottom-center':
        return { ...baseStyle, ...positionStyle, bottom: 0, left: '50%', transform: 'translateX(-50%)' };
      case 'bottom-right':
        return { ...baseStyle, ...positionStyle, bottom: 0, right: 0 };
      default:
        return { ...baseStyle, ...positionStyle, top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
    }
  };

  return {
    getImagePositionStyle
  };
};
