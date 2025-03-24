import { useState, useRef, useEffect } from 'react';
import { SCREEN_SIZES } from './ScreenSizeSelector';

export const useDebugImageContainer = () => {
  const [selectedScreenSize, setSelectedScreenSize] = useState<string>('Current Viewport');
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [containerWidth, setContainerWidth] = useState(0);
  const [containerHeight, setContainerHeight] = useState(0);
  const [containerPosition, setContainerPosition] = useState({ x: 540, y: 20 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = useState({ width: 480, height: 520 });
  const [isResizing, setIsResizing] = useState(false);
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, width: 0, height: 0 });
  
  // Get the selected screen size object
  const selectedSize = SCREEN_SIZES.find(s => s.name === selectedScreenSize) || SCREEN_SIZES[0];
  
  // Calculate aspect ratio
  const viewportRatio = selectedSize.width / selectedSize.height;

  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.target as HTMLImageElement;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
    
    // Update content dimensions after image is loaded
    if (contentRef.current) {
      setContainerWidth(contentRef.current.offsetWidth);
      setContainerHeight(contentRef.current.offsetHeight);
    }
  };

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!isResizing) {
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
    const handleMouseMove = (e: MouseEvent) => {
      if (isDragging) {
        const newX = Math.max(0, e.clientX - dragOffset.x);
        const newY = Math.max(0, e.clientY - dragOffset.y);
        
        // Get container dimensions
        const containerWidth = containerSize.width;
        const containerHeight = containerSize.height;
        
        // Calculate maximum positions to keep container on screen
        const maxX = window.innerWidth - containerWidth;
        const maxY = window.innerHeight - containerHeight;
        
        // Ensure container stays within viewport bounds
        setContainerPosition({ 
          x: Math.min(Math.max(0, newX), maxX), 
          y: Math.min(Math.max(0, newY), maxY) 
        });
      }
      
      if (isResizing) {
        const MIN_WIDTH = 320;
        const MIN_HEIGHT = 320;
        
        // Calculate new width and height
        const newWidth = Math.max(MIN_WIDTH, resizeStart.width + (e.clientX - resizeStart.x));
        
        // Ensure we maintain aspect ratio when resizing
        const newHeight = Math.round(newWidth / viewportRatio) + 60; // Add some padding for header
        
        // Ensure we don't resize beyond viewport
        const maxWidth = window.innerWidth - containerPosition.x;
        const maxHeight = window.innerHeight - containerPosition.y;
        
        const constrainedWidth = Math.min(newWidth, maxWidth);
        const constrainedHeight = Math.min(newHeight, maxHeight);
        
        setContainerSize({
          width: constrainedWidth,
          height: constrainedHeight
        });
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging, dragOffset, isResizing, resizeStart, containerPosition, viewportRatio, containerSize]);

  // Update container dimensions when screen size changes
  useEffect(() => {
    if (contentRef.current) {
      setContainerWidth(contentRef.current.offsetWidth);
      setContainerHeight(contentRef.current.offsetHeight);
      
      // Update container size based on aspect ratio
      const width = containerSize.width;
      const height = Math.round(width / viewportRatio) + 60; // Add some padding for header
      
      setContainerSize({
        width,
        height
      });
    }
  }, [selectedScreenSize, viewportRatio]);

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
