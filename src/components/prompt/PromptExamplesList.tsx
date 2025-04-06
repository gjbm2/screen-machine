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
  const [showAll, setShowAll] = useState(false);

  const visibleExamples = showAll ? examples : examples.slice(0, initialCount);

  const toggleShow = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setShowAll(!showAll);
  };

  if (examples.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1 items-center">
      <span className="text-xs text-muted-foreground">Try:</span>
      {visibleExamples.map((example, index) => (
        <Badge
          key={index}
          variant="outline"
          className="px-2 py-1 text-sm cursor-pointer hover:bg-blue-100 bg-blue-50 text-blue-700 border-blue-200 font-normal prompt-badge"
          onClick={() => onExampleClick(example)}
        >
          {example}
        </Badge>
      ))}

      {examples.length > initialCount && (
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleShow}
          className="h-6 text-[10px] px-1.5"
          type="button"
        >
          {showAll ? (
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
