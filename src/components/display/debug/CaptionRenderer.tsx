
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
  
  // Process the caption text - check if it contains a regex pattern
  const processedCaption = (() => {
    if (!caption) return null;
    
    try {
      // Check if caption is a regex pattern - must start with / and end with / or /flags
      const regexMatch = caption.match(/^\/(.+)\/([gimuy]*)$/);
      
      if (regexMatch) {
        const [_, pattern, flags] = regexMatch;
        console.log(`[CaptionRenderer] Processing regex pattern: ${pattern} with flags: ${flags}`);
        
        try {
          // Create a new RegExp object
          const regex = new RegExp(pattern, flags);
          
          // Generate a random string that matches the regex
          // This is a simple implementation and may not work for all regex patterns
          // For complex patterns, we'd need a more sophisticated regex-to-string generator
          
          // For now, we'll just return the pattern as a string
          return `[Regex: ${pattern}]`;
        } catch (err) {
          console.error('[CaptionRenderer] Invalid regex:', err);
          return `[Invalid regex: ${err.message}]`;
        }
      }
      
      // If not a regex, just return the original caption
      return caption;
    } catch (err) {
      console.error('[CaptionRenderer] Error processing caption:', err);
      return caption;
    }
  })();
  
  // Pre-process caption to handle newlines
  const captionLines = processedCaption ? processedCaption.split('\n') : [];

  // Calculate background opacity as a CSS rgba value
  const bgColorNoHash = captionBgColor.replace('#', '');
  const r = parseInt(bgColorNoHash.substring(0, 2) || '00', 16);
  const g = parseInt(bgColorNoHash.substring(2, 4) || '00', 16);
  const b = parseInt(bgColorNoHash.substring(4, 6) || '00', 16);
  const bgColorWithOpacity = `rgba(${r}, ${g}, ${b}, ${captionBgOpacity})`;

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
              color: `#${captionColor}`,
              fontSize: captionSize,
              fontFamily: captionFont,
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
