
import { DisplayParams } from '../types';

export const useImageStyler = () => {
  const getImagePositionStyle = (position: string, showMode: string): React.CSSProperties => {
    const baseStyle: React.CSSProperties = (() => {
      switch (showMode) {
        case 'fill':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          };
        case 'fit':
          return {
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
          };
        case 'stretch':
          return {
            width: '100%',
            height: '100%',
            objectFit: 'fill',
          };
        case 'actual':
          return {
            width: 'auto',
            height: 'auto',
            objectFit: 'none',
          };
        default:
          return {
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
          };
      }
    })();
    
    const positionStyle: React.CSSProperties = { position: 'absolute' };
    
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
