
import React from 'react';
import { CaptionPosition } from '../types';

interface CaptionRendererProps {
  caption: string;
  captionPosition: CaptionPosition;
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
  captionPosition,
  captionSize = '16px',
  captionColor = 'ffffff',
  captionFont = 'Arial, sans-serif',
  captionBgColor = '#000000',
  captionBgOpacity = 0.7,
  containerWidth,
  screenWidth
}) => {
  // Determine positioning based on captionPosition
  let positionClasses = '';
  let textAlignment = 'text-center';
  
  // Set horizontal alignment
  if (captionPosition.includes('left')) {
    textAlignment = 'text-left';
  } else if (captionPosition.includes('right')) {
    textAlignment = 'text-right';
  } else if (captionPosition.includes('center')) {
    textAlignment = 'text-center';
  }
  
  // Set position on screen
  if (captionPosition.startsWith('top')) {
    positionClasses = 'top-0 left-0 right-0';
  } else if (captionPosition.startsWith('middle')) {
    positionClasses = 'top-1/2 -translate-y-1/2 left-0 right-0';
  } else if (captionPosition.startsWith('bottom')) {
    positionClasses = 'bottom-0 left-0 right-0';
  }
  
  // Calculate max width based on screen size
  const maxWidth = Math.min(containerWidth, screenWidth);
  
  // Pre-process caption to handle newlines
  const captionLines = caption.split('\n');

  return (
    <div 
      className={`absolute p-2 ${positionClasses} ${textAlignment}`}
      style={{
        maxWidth: `${maxWidth}px`,
        marginLeft: 'auto',
        marginRight: 'auto',
        boxSizing: 'border-box',
      }}
    >
      <div 
        className="inline-block px-3 py-1" 
        style={{
          backgroundColor: captionBgColor,
          opacity: captionBgOpacity,
          borderRadius: '4px',
        }}
      >
        {captionLines.map((line, index) => (
          <div 
            key={index}
            style={{
              color: `#${captionColor}`,
              fontSize: captionSize,
              fontFamily: captionFont,
              lineHeight: '1.3',
              whiteSpace: 'pre-wrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              maxWidth: '100%',
            }}
          >
            {line || "\u00A0"}
          </div>
        ))}
      </div>
    </div>
  );
};
