
import React, { useState, useCallback, useRef, useEffect } from 'react';
import { X } from 'lucide-react';
import ConsoleOutput from './ConsoleOutput';

interface ResizableConsoleProps {
  logs: string[];
  isVisible: boolean;
  onClose: () => void;
}

const ResizableConsole: React.FC<ResizableConsoleProps> = ({ 
  logs, 
  isVisible, 
  onClose 
}) => {
  const [size, setSize] = useState(30); // Height in vh
  const [isDragging, setIsDragging] = useState(false);
  const startPosRef = useRef(0);
  const startSizeRef = useRef(0);
  const consoleRef = useRef<HTMLDivElement>(null);
  const animationFrameRef = useRef<number | null>(null);
  
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    // Only handle left click
    if (e.button !== 0) return;
    
    setIsDragging(true);
    startPosRef.current = e.clientY;
    startSizeRef.current = size;
    e.preventDefault();
  };
  
  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!isDragging) return;
    
    // Cancel any pending animation frame
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
    }
    
    // Schedule a new animation frame for the update
    animationFrameRef.current = requestAnimationFrame(() => {
      // Calculate the difference in mouse position
      const diff = startPosRef.current - e.clientY;
      
      // Convert the difference to vh units
      const diffVh = (diff / window.innerHeight) * 100;
      
      // Calculate the new size (percentage of viewport height)
      const newSize = Math.max(10, Math.min(70, startSizeRef.current + diffVh));
      
      setSize(newSize);
    });
  }, [isDragging]);
  
  const handleMouseUp = () => {
    setIsDragging(false);
    
    // Cancel any pending animation frame
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  };
  
  useEffect(() => {
    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    } else {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    }
    
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      
      // Clean up any pending animation frame
      if (animationFrameRef.current !== null) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isDragging, handleMouseMove]);
  
  if (!isVisible) return null;
  
  return (
    <div 
      ref={consoleRef}
      className="fixed bottom-0 left-0 right-0 bg-black text-white shadow-lg z-50 border-t border-gray-700 flex flex-col"
      style={{ height: `${size}vh` }}
    >
      <div 
        className="cursor-row-resize h-4 bg-gray-900 w-full flex items-center justify-center"
        onMouseDown={handleMouseDown}
      >
        <div className="w-16 h-1 bg-gray-600 rounded-full"></div>
      </div>
      
      <div className="flex justify-between items-center bg-gray-900 px-4 py-2">
        <h3 className="text-sm font-semibold">Console Output</h3>
        <button 
          className="text-gray-400 hover:text-white transition-colors"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      
      <div className="flex-1 overflow-auto p-4 bg-black">
        <ConsoleOutput logs={logs} />
      </div>
    </div>
  );
};

export default ResizableConsole;
