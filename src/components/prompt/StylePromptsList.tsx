
import React, { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface StylePrompt {
  display: string;
  prompt: string;
}

interface StylePromptsListProps {
  styles: StylePrompt[];
  initialCount: number;
  onStyleClick: (style: StylePrompt) => void;
}

const StylePromptsList: React.FC<StylePromptsListProps> = ({ 
  styles, 
  initialCount, 
  onStyleClick 
}) => {
  const [showAllStyles, setShowAllStyles] = useState(false);
  
  const visibleStyles = showAllStyles
    ? styles
    : styles.slice(0, initialCount);
    
  const toggleStyles = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setShowAllStyles(!showAllStyles);
  };
  
  if (styles.length === 0) {
    return null;
  }
  
  return (
    <div className="flex flex-wrap gap-1 items-center">
      <span className="text-xs text-muted-foreground">Style:</span>
      {visibleStyles.map((style, index) => (
        <Badge 
          key={index}
          variant="outline"
          className="px-2 py-1 text-sm cursor-pointer hover:bg-amber-100 bg-amber-50 text-amber-700 border-amber-200 font-normal prompt-badge"
		  onClick={() => onStyleClick(style)}
        >
          {style.display}
        </Badge>
      ))}
      
      {styles.length > initialCount && (
        <Button 
          variant="ghost" 
          size="sm" 
          onClick={toggleStyles}
          className="h-6 text-[10px] px-1.5"
          type="button"
        >
          {showAllStyles ? (
            <>
              <ChevronUp className="mr-1 h-3 w-3" />
              Less
            </>
          ) : (
            <>
              <ChevronDown className="mr-1 h-3 w-3" />
              More
            </>
          )}
        </Button>
      )}
    </div>
  );
};

export default StylePromptsList;
