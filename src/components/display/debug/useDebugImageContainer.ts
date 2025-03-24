
import { useState, useRef, useEffect } from 'react';

export const useDebugImageContainer = () => {
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [containerWidth, setContainerWidth] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);
  const [containerPosition, setContainerPosition] = useState({ x: 50, y: 50 });
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [containerSize, setContainerSize] = useState({ width: 400, height: 400 });
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  const [selectedSize, setSelectedSize] = useState({ name: 'Current Viewport', width: window.innerWidth, height: window.innerHeight });
  const [viewportRatio, setViewportRatio] = useState(window.innerWidth / window.innerHeight);
  
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    
    if (contentRef.current) {
      setContainerWidth(contentRef.current.offsetWidth);
      setContainerHeight(contentRef.current.offsetHeight);
    }
  };
  
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    
    if (containerRef.current) {
      setIsDragging(true);
      setDragOffset({
        x: e.clientX - containerPosition.x,
        y: e.clientY - containerPosition.y,
      });
    }
    
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  };
  
  const handleMouseMove = (e: MouseEvent) => {
    if (isDragging) {
      setContainerPosition({
        x: e.clientX - dragOffset.x,
        y: e.clientY - dragOffset.y,
      });
    } else if (isResizing) {
      const newWidth = Math.max(200, resizeStart.width + (e.clientX - resizeStart.x));
      const newHeight = Math.max(200, resizeStart.height + (e.clientY - resizeStart.y));
      
      setContainerSize({
        width: newWidth,
        height: newHeight,
      });
    }
  };
  
  const handleMouseUp = () => {
    setIsDragging(false);
    setIsResizing(false);
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
  };
  
  const handleResizeStart = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsResizing(true);
    setResizeStart({
      x: e.clientX,
      y: e.clientY,
      width: containerSize.width,
      height: containerSize.height,
    });
    
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  };
  
  useEffect(() => {
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, isResizing]);
  
  useEffect(() => {
    if (contentRef.current) {
      setContainerWidth(contentRef.current.offsetWidth);
      setContainerHeight(contentRef.current.offsetHeight);
    }
    
    const handleResize = () => {
      if (contentRef.current) {
        setContainerWidth(contentRef.current.offsetWidth);
        setContainerHeight(contentRef.current.offsetHeight);
      }
      setViewportRatio(window.innerWidth / window.innerHeight);
      
      // Update current viewport dimensions
      if (selectedSize.name === 'Current Viewport') {
        setSelectedSize({
          name: 'Current Viewport',
          width: window.innerWidth,
          height: window.innerHeight
        });
      }
    };
    
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [contentRef, selectedSize.name]);

  return {
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
    setContainerSize,
    setSelectedSize,
    handleImageLoad,
    handleMouseDown,
    handleResizeStart
  };
};
