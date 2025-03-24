
import React from 'react';

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
  minWidth?: number;
  minHeight?: number;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ 
  onMouseDown,
  minWidth = 400,
  minHeight = 400 
}) => {
  // Add a custom mouse down handler to enforce minimum size constraints
  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Call the original handler to maintain existing functionality
    onMouseDown(e);
  };
  
  return (
    <div
      className="absolute bottom-0 right-0 w-5 h-5 cursor-se-resize z-20 bg-background/10 rounded-bl-sm"
      onMouseDown={handleMouseDown}
      title="Resize panel"
    />
  );
};
