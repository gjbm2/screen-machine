import React from 'react';
import { CardContent } from '@/components/ui/card';
import { useIsMobile } from '@/hooks/useIsMobile';

interface HierarchicalContentProps {
  level: 1 | 2 | 3;
  children: React.ReactNode;
  className?: string;
}

export const HierarchicalContent: React.FC<HierarchicalContentProps> = ({ 
  level, 
  children, 
  className = "" 
}) => {
  const isMobile = useIsMobile();
  
  // Define padding based on hierarchy level
  const getPaddingClass = () => {
    switch (level) {
      case 1:
        return isMobile ? "p-2" : "p-6";
      case 2:
        return isMobile ? "p-2" : "p-4";
      case 3:
        return isMobile ? "p-2" : "p-3";
      default:
        return isMobile ? "p-2" : "p-4";
    }
  };
  
  return (
    <CardContent className={`${getPaddingClass()} ${className}`}>
      {children}
    </CardContent>
  );
}; 