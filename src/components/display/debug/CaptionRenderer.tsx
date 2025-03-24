
import React from 'react';
import { CaptionPosition } from '../types';

interface CaptionRendererProps {
  caption: string;
  position: CaptionPosition;
  fontSize: string;
  color: string;
  fontFamily: string;
  backgroundColor: string;
  backgroundOpacity: number;
  containerWidth: number;
  screenWidth: number;
  screenSize?: { width: number; height: number };
}

export const CaptionRenderer: React.FC<CaptionRendererProps> = ({
  caption,
  position,
  fontSize,
  color,
  fontFamily,
  backgroundColor,
  backgroundOpacity,
  containerWidth,
  screenWidth,
  screenSize
}) => {
  // Ensure background color has # prefix
  const bgColor = backgroundColor.startsWith('#') ? backgroundColor : `#${backgroundColor}`;
  
  // Calculate background opacity as hex for rgba
  const bgOpacityHex = Math.round(backgroundOpacity * 255).toString(16).padStart(2, '0');
  const bgColorWithOpacity = `${bgColor}${bgOpacityHex}`;
  
  console.log('[CaptionRenderer] Rendering caption with:', {
    position,
    fontSize,
    color,
    fontFamily,
    bgColor,
    backgroundOpacity,
    bgOpacityHex,
    bgColorWithOpacity,
    screenSize
  });
  
  // Calculate scaled font size based on container dimensions and screen size
  const getScaledFontSize = () => {
    // Extract numeric portion and unit
    const matches = fontSize.match(/^(\d+(?:\.\d+)?)([a-z%]+)?$/i);
    if (!matches) return fontSize;
    
    const size = parseFloat(matches[1]);
    const unit = matches[2] || 'px';
    
    // If we have a specified screen size, use that for scaling
    if (screenSize && screenSize.width > 0) {
      // Reference width (Full HD is a common reference)
      const referenceWidth = 1920;
      const scaleFactor = containerWidth / referenceWidth;
      
      // Apply scaling but limit to reasonable bounds
      // Allowing larger font sizes than before (up to 120px)
      const scaledSize = Math.max(10, Math.min(120, size * scaleFactor));
      return `${scaledSize}${unit}`;
    }
    
    // Fallback to screenWidth-based scaling if no specific size is provided
    if (screenWidth > 0 && containerWidth > 0) {
      const scaleFactor = containerWidth / screenWidth;
      const scaledSize = Math.max(10, Math.min(120, size * scaleFactor));
      return `${scaledSize}${unit}`;
    }
    
    return fontSize;
  };
  
  // Base styles for the caption
  const baseStyles: React.CSSProperties = {
    padding: '8px 16px',
    backgroundColor: bgColorWithOpacity,
    color: color.startsWith('#') ? color : `#${color}`,
    fontSize: getScaledFontSize(),
    fontFamily,
    textAlign: 'center',
    borderRadius: '4px',
    maxWidth: '80%',
    whiteSpace: caption.includes('\n') ? 'pre-line' : 'normal',
    wordBreak: 'break-word',
    zIndex: 10,
    position: 'absolute'
  };
  
  // Position-specific styles
  const getPositionStyles = (): React.CSSProperties => {
    switch (position) {
      case 'top-left':
        return { top: '20px', left: '20px' };
      case 'top-center':
        return { top: '20px', left: '50%', transform: 'translateX(-50%)' };
      case 'top-right':
        return { top: '20px', right: '20px' };
      case 'middle-left':
        return { top: '50%', left: '20px', transform: 'translateY(-50%)' };
      case 'middle-center':
        return { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' };
      case 'middle-right':
        return { top: '50%', right: '20px', transform: 'translateY(-50%)' };
      case 'bottom-left':
        return { bottom: '20px', left: '20px' };
      case 'bottom-center':
        return { bottom: '20px', left: '50%', transform: 'translateX(-50%)' };
      case 'bottom-right':
        return { bottom: '20px', right: '20px' };
      default:
        return { bottom: '20px', left: '50%', transform: 'translateX(-50%)' };
    }
  };
  
  // Merge base styles with position-specific styles
  const finalStyles = { ...baseStyles, ...getPositionStyles() };
  
  return (
    <div style={finalStyles}>
      {caption}
    </div>
  );
};
