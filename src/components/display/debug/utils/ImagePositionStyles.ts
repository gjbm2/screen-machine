
import { ShowMode, PositionMode } from '../../types';
import React from 'react';

export const getImageStyle = (
  showMode: ShowMode,
  position: PositionMode,
  imageDimensions: { width: number; height: number }
): React.CSSProperties => {
  let style: React.CSSProperties = {
    maxWidth: '100%',
    maxHeight: '100%',
  };
  
  switch (showMode) {
    case 'fill':
      style = {
        ...style,
        width: '100%',
        height: '100%',
        objectFit: 'cover',
        objectPosition: position,
      };
      break;
    case 'fit':
      style = {
        ...style,
        width: 'auto',
        height: 'auto',
        maxWidth: '100%',
        maxHeight: '100%',
        objectFit: 'contain',
        position: 'absolute',
      };
      break;
    case 'stretch':
      style = {
        ...style,
        width: '100%',
        height: '100%',
        objectFit: 'fill',
      };
      break;
    case 'actual':
      style = {
        ...style,
        width: imageDimensions.width > 0 ? `${imageDimensions.width}px` : 'auto',
        height: imageDimensions.height > 0 ? `${imageDimensions.height}px` : 'auto',
        objectFit: 'none',
      };
      break;
    default:
      style = {
        ...style,
        objectFit: 'contain',
      };
  }
  
  // Apply position to fit and actual modes
  if (showMode === 'fit' || showMode === 'actual') {
    switch (position) {
      case 'top-left':
        style.top = 0;
        style.left = 0;
        break;
      case 'top-center':
        style.top = 0;
        style.left = '50%';
        style.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        style.top = 0;
        style.right = 0;
        break;
      case 'center-left':
        style.top = '50%';
        style.left = 0;
        style.transform = 'translateY(-50%)';
        break;
      case 'center':
        style.top = '50%';
        style.left = '50%';
        style.transform = 'translate(-50%, -50%)';
        break;
      case 'center-right':
        style.top = '50%';
        style.right = 0;
        style.transform = 'translateY(-50%)';
        break;
      case 'bottom-left':
        style.bottom = 0;
        style.left = 0;
        break;
      case 'bottom-center':
        style.bottom = 0;
        style.left = '50%';
        style.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        style.bottom = 0;
        style.right = 0;
        break;
      default:
        style.top = '50%';
        style.left = '50%';
        style.transform = 'translate(-50%, -50%)';
    }
  }
  
  return style;
};
