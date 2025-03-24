
import React from 'react';
import { MoveDiagonal } from 'lucide-react';

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
  minWidth?: number;
  minHeight?: number;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ 
  onMouseDown,
  minWidth = 300,
  minHeight = 200 
}) => {
  // Add a custom mouse down handler to enforce minimum size constraints
  const handleMouseDown = (e: React.MouseEvent) => {
    console.log('ResizeHandle: Mouse down event triggered');
    
    // Store original dimensions to apply constraints
    const container = (e.currentTarget as HTMLElement).closest('.resizable-container');
    
    if (container) {
      const originalWidth = container.clientWidth;
      const originalHeight = container.clientHeight;
      
      console.log(`ResizeHandle: Original dimensions - ${originalWidth}x${originalHeight}`);
      
      // Create a tracking function to enforce minimum size during resize
      const trackMouseMove = (moveEvent: MouseEvent) => {
        const currentWidth = container.clientWidth;
        const currentHeight = container.clientHeight;
        
        // If dimensions are getting too small, enforce minimum size
        if (currentWidth < minWidth || currentHeight < minHeight) {
          console.log(`ResizeHandle: Enforcing minimum size: ${minWidth}x${minHeight}`);
          
          // Set minimum dimensions - fix the Element type by casting to HTMLElement
          if (currentWidth < minWidth) {
            (container as HTMLElement).style.width = `${minWidth}px`;
          }
          
          if (currentHeight < minHeight) {
            (container as HTMLElement).style.height = `${minHeight}px`;
          }
        }
      };
      
      // Add the tracking function to the document
      document.addEventListener('mousemove', trackMouseMove);
      
      // Remove tracking when mouse is released
      const cleanupTracking = () => {
        document.removeEventListener('mousemove', trackMouseMove);
        document.removeEventListener('mouseup', cleanupTracking);
      };
      
      document.addEventListener('mouseup', cleanupTracking, { once: true });
    }
    
    // Call the original handler to maintain existing functionality
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
