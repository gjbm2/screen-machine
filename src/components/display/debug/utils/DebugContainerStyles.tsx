
import React from 'react';

interface ContainerStylesProps {
  isFixedPanel: boolean;
  containerPosition: { x: number; y: number };
  containerSize: { width: number; height: number };
  isDragging: boolean;
}

export const getContainerStyles = ({
  isFixedPanel,
  containerPosition,
  containerSize,
  isDragging
}: ContainerStylesProps): { cardStyles: string; innerStyles: React.CSSProperties } => {
  const cardStyles = isFixedPanel 
    ? "h-full w-full overflow-hidden flex flex-col" 
    : "absolute z-10 cursor-grab overflow-visible resizable-container";

  const innerStyles: React.CSSProperties = isFixedPanel 
    ? {} 
    : { 
        left: `${containerPosition.x}px`, 
        top: `${containerPosition.y}px`,
        width: `${containerSize.width}px`,
        height: `${containerSize.height}px`,
        cursor: isDragging ? 'grabbing' : 'grab',
        resize: 'none' as const
      };

  return { cardStyles, innerStyles };
};
