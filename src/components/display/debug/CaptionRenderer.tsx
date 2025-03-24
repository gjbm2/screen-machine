
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
  screenWidth
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
    bgColorWithOpacity
  });
  
  // Base styles for the caption
  const baseStyles: React.CSSProperties = {
    padding: '8px 16px',
    backgroundColor: bgColorWithOpacity,
    color: `#${color}`,
    fontSize,
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
  
  // Apply responsive font scaling for very small screens
  if (screenWidth < 640 && containerWidth > 0) {
    const scaleFactor = containerWidth / screenWidth;
    const baseFontSize = parseFloat(fontSize);
    const unit = fontSize.replace(/[\d.]/g, '');
    finalStyles.fontSize = `${Math.min(baseFontSize, baseFontSize * scaleFactor)}${unit || 'px'}`;
  }
  
  return (
    <div style={finalStyles}>
      {caption}
    </div>
  );
};
