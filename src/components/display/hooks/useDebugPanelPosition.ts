
import { useState, useRef, useEffect } from 'react';

export const useDebugPanelPosition = () => {
  const [position, setPosition] = useState({ x: 4, y: 4 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const panelRef = useRef<HTMLDivElement>(null);
  
  const [isResizing, setIsResizing] = useState(false);
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const [panelSize, setPanelSize] = useState({ width: '480px', height: 'auto' });

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target instanceof Element && e.target.closest('.card-header-drag-handle')) {
      setIsDragging(true);
      const rect = panelRef.current?.getBoundingClientRect();
      if (rect) {
        setDragOffset({
          x: e.clientX - rect.left,
          y: e.clientY - rect.top
        });
      }
    }
  };

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: panelRef.current?.offsetWidth || 480,
      height: panelRef.current?.offsetHeight || 600
    });
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      const newX = Math.max(0, e.clientX - dragOffset.x);
      const newY = Math.max(0, e.clientY - dragOffset.y);
      
      const maxX = window.innerWidth - (panelRef.current?.offsetWidth || 480);
      const maxY = window.innerHeight - (panelRef.current?.offsetHeight || 400);
      
      setPosition({ 
        x: Math.min(newX, maxX), 
        y: Math.min(newY, maxY) 
      });
    }
    
    if (isResizing) {
      const MIN_WIDTH = 400;
      const MIN_HEIGHT = 400;
      
      const newWidth = Math.max(MIN_WIDTH, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(MIN_HEIGHT, resizeStart.height + (e.clientY - resizeStart.y));
      
      const maxWidth = window.innerWidth - position.x;
      const maxHeight = window.innerHeight - position.y;
      
      setPanelSize({ 
        width: `${Math.min(newWidth, maxWidth)}px`, 
        height: `${Math.min(newHeight, maxHeight)}px` 
      });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    setIsResizing(false);
  };

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset, isResizing, resizeStart, position]);

  return {
    position,
    setPosition,
    isDragging,
    setIsDragging,
    dragOffset,
    setDragOffset,
    panelRef,
    panelSize,
    setPanelSize,
    isResizing,
    setIsResizing,
    resizeStart,
    setResizeStart,
    handleMouseDown,
    handleResizeStart
  };
};
