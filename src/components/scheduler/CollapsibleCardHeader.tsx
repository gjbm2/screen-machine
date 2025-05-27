import React from 'react';
import { CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useIsMobile } from '@/hooks/useIsMobile';
import { CollapsibleCardHeaderProps } from '@/types/scheduler';

export const CollapsibleCardHeader: React.FC<CollapsibleCardHeaderProps> = ({ 
  title, 
  isOpen, 
  onToggle,
  count
}) => {
  const isMobile = useIsMobile();
  
  return (
    <CardHeader 
      className={`cursor-pointer hover:bg-muted/50 transition-colors ${
        isMobile ? 'p-3' : 'p-6'
      }`} 
      onClick={onToggle}
    >
      <div className="flex items-center justify-between">
        <CardTitle className="text-lg sm:text-xl font-bold flex items-center">
          {isOpen ? (
            <ChevronDown className="h-4 w-4 sm:h-5 sm:w-5 mr-2 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 sm:h-5 sm:w-5 mr-2 text-muted-foreground" />
          )}
          {title}
          {count !== undefined && (
            <Badge variant="secondary" className="ml-2 text-xs">
              {count}
            </Badge>
          )}
        </CardTitle>
      </div>
    </CardHeader>
  );
}; 