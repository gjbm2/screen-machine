
import React, { useEffect, useRef } from 'react';
import { MoveDiagonal } from 'lucide-react';

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
  minWidth?: number;
  minHeight?: number;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ 
  onMouseDown,
  minWidth = 300,
  minHeight = 400 
}) => {
  const handleMouseDown = (e: React.MouseEvent) => {
    console.log('ResizeHandle: Mouse down event triggered');
    console.log(`ResizeHandle: Enforcing minimum size: ${minWidth}x${minHeight}`);
    
    // Get the container element
    const container = (e.currentTarget as HTMLElement).closest('.resizable-container');
    
    // Create a resize observer to ensure minimum size
    if (container) {
      const resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const { width, height } = entry.contentRect;
          
          // Apply minimum dimensions if needed
          if (width < minWidth) {
            (container as HTMLElement).style.width = `${minWidth}px`;
          }
          
          if (height < minHeight) {
            (container as HTMLElement).style.height = `${minHeight}px`;
          }
        }
      });
      
      // Start observing
      resizeObserver.observe(container);
      
      // Clean up observer on mouse up
      const cleanup = () => {
        resizeObserver.disconnect();
        document.removeEventListener('mouseup', cleanup);
      };
      
      document.addEventListener('mouseup', cleanup, { once: true });
    }
    
    // Call the original handler
    onMouseDown(e);
  };
  
  return (
    <div
      className="absolute bottom-0 right-0 w-6 h-6 cursor-se-resize z-20 flex items-center justify-center"
      onMouseDown={handleMouseDown}
    >
      <MoveDiagonal className="h-4 w-4 text-gray-400" />
    </div>
  );
};
