
import { useState, useRef, useEffect } from 'react';
import { ShowMode, PositionMode } from '../types';
import { SCREEN_SIZES } from './ScreenSizeSelector';

export const useDebugImageContainer = () => {
  const [selectedScreenSize, setSelectedScreenSize] = useState<string>('Current Viewport');
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [containerWidth, setContainerWidth] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);
  
  const [containerPosition, setContainerPosition] = useState({ x: window.innerWidth / 2 - 300, y: 200 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  
  const [containerSize, setContainerSize] = useState({ width: 600, height: 400 });
  const [isResizing, setIsResizing] = useState(false);
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  
  const selectedSize = SCREEN_SIZES.find(size => size.name === selectedScreenSize) || SCREEN_SIZES[0];
  const viewportRatio = selectedSize.width / selectedSize.height;
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };
  
  useEffect(() => {
    if (contentRef.current) {
      const rect = contentRef.current.getBoundingClientRect();
      setContainerWidth(rect.width);
      setContainerHeight(rect.height);
    }
  }, [containerSize, selectedScreenSize]);

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target instanceof Element && e.target.closest('.card-header-drag-handle')) {
      setIsDragging(true);
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        setDragOffset({
          x: e.clientX - rect.left,
          y: e.clientY - rect.top
        });
      }
    }
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      const newX = e.clientX - dragOffset.x;
      const newY = e.clientY - dragOffset.y;
      setContainerPosition({ x: newX, y: newY });
    }
    
    if (isResizing) {
      const newWidth = Math.max(300, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(200, resizeStart.height + (e.clientY - resizeStart.y));
      setContainerSize({ width: newWidth, height: newHeight });
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
    setIsResizing(false);
  };
  
  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: containerSize.width,
      height: containerSize.height
    });
  };

  useEffect(() => {
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset, isResizing, resizeStart]);

  // This method will update the selectedScreenSize state with a name string
  const updateSelectedScreenSize = (name: string) => {
    setSelectedScreenSize(name);
  };

  // This method takes the full size object and just extracts the name to update the state
  const setSelectedScreenSizeObject = (size: { name: string; width: number; height: number }) => {
    setSelectedScreenSize(size.name);
  };

  return {
    selectedScreenSize,
    setSelectedScreenSize: updateSelectedScreenSize,
    setSelectedScreenSizeObject,
    imageDimensions,
    containerWidth,
    containerHeight,
    containerPosition,
    isDragging,
    containerRef,
    contentRef,
    containerSize,
    selectedSize,
    viewportRatio,
    handleImageLoad,
    handleMouseDown,
    handleResizeStart
  };
};
