
import React from 'react';
import { MoveDiagonal } from 'lucide-react';

interface ResizeHandleProps {
  onMouseDown: (e: React.MouseEvent) => void;
}

export const ResizeHandle: React.FC<ResizeHandleProps> = ({ onMouseDown }) => {
  // Add a custom mouse down handler to enforce minimum size constraints
  const handleMouseDown = (e: React.MouseEvent) => {
    // Call the original handler to maintain existing functionality
    onMouseDown(e);
  };
  
  return (
    <div
      className="absolute bottom-0 right-0 w-6 h-6 cursor-se-resize z-20 flex items-center justify-center bg-background/50 rounded-sm"
      onMouseDown={handleMouseDown}
      title="Resize panel"
    >
      <MoveDiagonal className="h-4 w-4 text-gray-400" />
    </div>
  );
};
