
import { ShowMode, PositionMode } from '../types';
import React from 'react';

export const getPositioningStyles = (
  pos: PositionMode, 
  mode: ShowMode, 
  imageDimensions: { width: number; height: number }
): React.CSSProperties => {
  let styles: React.CSSProperties = {
    position: 'absolute',
  };
  
  if (mode === 'actual' && imageDimensions.width > 0 && imageDimensions.height > 0) {
    styles.width = `${imageDimensions.width}px`;
    styles.height = `${imageDimensions.height}px`;
    styles.objectFit = 'none';
    
    switch(pos) {
      case 'top-left':
        styles.top = '0';
        styles.left = '0';
        break;
      case 'top-center':
        styles.top = '0';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'top-right':
        styles.top = '0';
        styles.right = '0';
        break;
      case 'center-left':
        styles.top = '50%';
        styles.left = '0';
        styles.transform = 'translateY(-50%)';
        break;
      case 'center':
        styles.top = '50%';
        styles.left = '50%';
        styles.transform = 'translate(-50%, -50%)';
        break;
      case 'center-right':
        styles.top = '50%';
        styles.right = '0';
        styles.transform = 'translateY(-50%)';
        break;
      case 'bottom-left':
        styles.bottom = '0';
        styles.left = '0';
        break;
      case 'bottom-center':
        styles.bottom = '0';
        styles.left = '50%';
        styles.transform = 'translateX(-50%)';
        break;
      case 'bottom-right':
        styles.bottom = '0';
        styles.right = '0';
        break;
      default:
        styles.top = '50%';
        styles.left = '50%';
        styles.transform = 'translate(-50%, -50%)';
    }
    
    return styles;
  }
  
  switch (mode) {
    case 'fill':
      styles = {
        ...styles,
        width: '100%',
        height: '100%',
        objectFit: 'cover',
      };
      break;
    
    case 'fit':
      styles = {
        ...styles,
        maxWidth: '100%',
        maxHeight: '100%',
        width: 'auto',
        height: 'auto',
        objectFit: 'contain',
      };
      break;
    
    case 'stretch':
      styles = {
        ...styles,
        width: '100%',
        height: '100%',
        objectFit: 'fill',
      };
      break;
    
    default:
      styles = {
        ...styles,
        maxWidth: '100%',
        maxHeight: '100%',
        objectFit: 'contain',
      };
  }
  
  switch(pos) {
    case 'top-left':
      styles.top = '0';
      styles.left = '0';
      break;
    case 'top-center':
      styles.top = '0';
      styles.left = '50%';
      styles.transform = 'translateX(-50%)';
      break;
    case 'top-right':
      styles.top = '0';
      styles.right = '0';
      break;
    case 'center-left':
      styles.top = '50%';
      styles.left = '0';
      styles.transform = 'translateY(-50%)';
      break;
    case 'center':
      styles.top = '50%';
      styles.left = '50%';
      styles.transform = 'translate(-50%, -50%)';
      break;
    case 'center-right':
      styles.top = '50%';
      styles.right = '0';
      styles.transform = 'translateY(-50%)';
      break;
    case 'bottom-left':
      styles.bottom = '0';
      styles.left = '0';
      break;
    case 'bottom-center':
      styles.bottom = '0';
      styles.left = '50%';
      styles.transform = 'translateX(-50%)';
      break;
    case 'bottom-right':
      styles.bottom = '0';
      styles.right = '0';
      break;
    default:
      styles.top = '50%';
      styles.left = '50%';
      styles.transform = 'translate(-50%, -50%)';
  }
  
  return styles;
};
