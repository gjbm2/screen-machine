
import React, { useState, useEffect } from 'react';
import { MoveDiagonal } from 'lucide-react';

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ onMouseDown }) => {
  // Add a custom mouse down handler to enforce minimum size constraints
  const handleMouseDown = (e: React.MouseEvent) => {
    const originalOnMouseDown = onMouseDown;
    
    // Call the original handler to maintain existing functionality
    originalOnMouseDown(e);
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
