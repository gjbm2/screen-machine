
import React, { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface PromptExamplesListProps {
  examples: string[];
  initialCount: number;
  onExampleClick: (example: string) => void;
}

const PromptExamplesList: React.FC<PromptExamplesListProps> = ({ 
  examples, 
  initialCount, 
  onExampleClick 
}) => {
  const [showAllExamples, setShowAllExamples] = useState(false);
  
  const visibleExamples = showAllExamples 
    ? examples 
    : examples.slice(0, initialCount);
    
  const toggleExamples = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setShowAllExamples(!showAllExamples);
  };
  
  if (examples.length === 0) {
    return null;
  }
  
  return (
    <div className="flex flex-wrap gap-1 items-center">
      <span className="text-xs text-muted-foreground">Try:</span>
      {visibleExamples.map((example, index) => (
        <Badge 
          key={index}
          variant="secondary"
          className="px-2 py-1 text-sm cursor-pointer hover:bg-secondary/80 font-normal prompt-badge"
          onClick={() => onExampleClick(example)}
        >
          {example}
        </Badge>
      ))}
      
      {examples.length > initialCount && (
        <Button 
          variant="ghost" 
          size="sm" 
          onClick={toggleExamples}
          className="h-7 text-xs px-1.5"
          type="button"
        >
          {showAllExamples ? (
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

export default PromptExamplesList;
