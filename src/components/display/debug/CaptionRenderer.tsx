
import React from 'react';
import { CaptionPosition } from '../types';

interface CaptionRendererProps {
  caption: string | null;
  captionPosition?: CaptionPosition;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
  captionBgColor?: string;
  captionBgOpacity?: number;
  containerWidth: number;
  screenWidth: number;
}

export const CaptionRenderer: React.FC<CaptionRendererProps> = ({
  caption,
  captionPosition = 'bottom-center',
  captionSize = '16px',
  captionColor = 'ffffff',
  captionFont = 'Arial, sans-serif',
  captionBgColor = '#000000',
  captionBgOpacity = 0.7,
  containerWidth,
  screenWidth
}) => {
  if (!caption) return null;

  const getCaptionScaledFontSize = (baseSize: string) => {
    const matches = baseSize.match(/^(\d+(?:\.\d+)?)([a-z%]+)?$/i);
    if (!matches) return baseSize;
    
    const size = parseFloat(matches[1]);
    const unit = matches[2] || 'px';
    
    const scaleFactor = containerWidth / screenWidth;
    
    const scaledSize = Math.max(8, Math.min(32, size * scaleFactor));
    
    return `${scaledSize}${unit}`;
  };

  const getCaptionStyles = (): React.CSSProperties => {
    const scaledFontSize = getCaptionScaledFontSize(captionSize);
    
    // Calculate background opacity - convert to hex
    const bgOpacityHex = Math.round((captionBgOpacity || 0.7) * 255).toString(16).padStart(2, '0');
    const bgColor = `${captionBgColor}${bgOpacityHex}`;
    
    const styles: React.CSSProperties = {
      position: 'absolute',
      padding: '8px 16px',
      backgroundColor: bgColor,
      color: `#${captionColor}`,
      fontSize: scaledFontSize,
      fontFamily: captionFont,
      maxWidth: '80%',
      textAlign: 'center',
      borderRadius: '4px',
      zIndex: 10,
      whiteSpace: caption?.includes('\n') ? 'pre-line' : 'normal',
    };
    
    if (captionPosition?.includes('top')) {
      styles.top = '10px';
    } else if (captionPosition?.includes('bottom')) {
      styles.bottom = '10px';
    } else {
      styles.top = '50%';
      styles.transform = 'translateY(-50%)';
    }
    
    if (captionPosition?.includes('left')) {
      styles.left = '10px';
    } else if (captionPosition?.includes('right')) {
      styles.right = '10px';
    } else {
      styles.left = '50%';
      styles.transform = captionPosition === 'bottom-center' || captionPosition === 'top-center' ? 
        'translateX(-50%)' : styles.transform || 'none';
      
      if (captionPosition && !captionPosition.includes('-')) {
        styles.transform = 'translate(-50%, -50%)';
      }
    }
    
    return styles;
  };

  return (
    <div style={getCaptionStyles()}>
      {caption}
    </div>
  );
};
