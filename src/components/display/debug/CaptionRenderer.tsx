
import React from 'react';
import { CaptionPosition } from '../types';

interface CaptionRendererProps {
  caption: string;
  captionPosition: CaptionPosition; // Changed from 'position' to 'captionPosition'
  fontSize?: string;
  color?: string;
  fontFamily?: string;
  backgroundColor?: string;
  backgroundOpacity?: number;
  containerWidth?: number;
  screenWidth?: number;
}

export const CaptionRenderer: React.FC<CaptionRendererProps> = ({
  caption,
  captionPosition, // Changed from 'position' to 'captionPosition'
  fontSize = '16px',
  color = 'ffffff',
  fontFamily = 'Arial, sans-serif',
  backgroundColor = '#000000',
  backgroundOpacity = 0.7,
  containerWidth = window.innerWidth,
  screenWidth = window.innerWidth
}) => {
  // Determine positioning based on position
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
  
  // Process the caption text
  const processedCaption = caption || '';
  
  // Pre-process caption to handle newlines
  const captionLines = processedCaption.split('\n');

  // Calculate background opacity as a CSS rgba value
  const bgColorNoHash = backgroundColor.replace('#', '');
  const r = parseInt(bgColorNoHash.substring(0, 2) || '00', 16);
  const g = parseInt(bgColorNoHash.substring(2, 4) || '00', 16);
  const b = parseInt(bgColorNoHash.substring(4, 6) || '00', 16);
  const bgColorWithOpacity = `rgba(${r}, ${g}, ${b}, ${backgroundOpacity})`;

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
          backgroundColor: bgColorWithOpacity,
          borderRadius: '4px',
        }}
      >
        {captionLines.map((line, index) => (
          <div 
            key={index}
            style={{
              color: `#${color}`,
              fontSize: fontSize,
              fontFamily: fontFamily,
              lineHeight: '1.3',
              whiteSpace: 'pre-wrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              maxWidth: '100%',
              textAlign: captionPosition.includes('left') ? 'left' : 
                        captionPosition.includes('right') ? 'right' : 'center',
            }}
          >
            {line || "\u00A0"}
          </div>
        ))}
      </div>
    </div>
  );
};
