
import React, { useState } from 'react';
import examplePrompts from '@/data/example-prompts.json';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface PromptExamplesProps {
  prompt: string;
  onExampleClick: (example: string) => void;
  onStyleClick: (prompt: string) => void;
  showMore?: boolean;
}

const PromptExamples: React.FC<PromptExamplesProps> = ({ 
  prompt, 
  onExampleClick, 
  onStyleClick,
  showMore = false
}) => {
  const [showAllExamples, setShowAllExamples] = useState(false);
  const [showAllStyles, setShowAllStyles] = useState(false);
  
  // Get the initial number of examples to show based on screen size
  const initialExamplesCount = showMore ? 4 : 2;
  const initialStylesCount = showMore ? 6 : 4;
  
  const handleExampleClick = (example: string) => {
    onExampleClick(example);
  };
  
  const handleStyleClick = (stylePrompt: string) => {
    const combinedPrompt = prompt ? `${prompt}, ${stylePrompt}` : stylePrompt;
    onStyleClick(combinedPrompt);
  };
  
  const toggleExamples = () => {
    setShowAllExamples(!showAllExamples);
  };
  
  const toggleStyles = () => {
    setShowAllStyles(!showAllStyles);
  };
  
  const renderExamples = () => {
    const examplesList = examplePrompts.examples || [];
    
    const visibleExamples = showAllExamples 
      ? examplesList 
      : examplesList.slice(0, initialExamplesCount);
      
    return (
      <div className="mb-2">
        <div className="flex flex-wrap gap-1.5">
          {visibleExamples.map((example, index) => (
            <Badge 
              key={index}
              variant="secondary"
              className="px-3 py-1.5 text-sm cursor-pointer hover:bg-secondary/80 font-normal"
              onClick={() => handleExampleClick(example)}
            >
              {example}
            </Badge>
          ))}
          
          {examplesList.length > initialExamplesCount && (
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={toggleExamples}
              className="h-8 text-xs"
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
      </div>
    );
  };
  
  const renderStyles = () => {
    const stylesList = examplePrompts.styles || [];
    
    const visibleStyles = showAllStyles
      ? stylesList
      : stylesList.slice(0, initialStylesCount);
      
    return (
      <div>
        <div className="flex flex-wrap gap-1.5">
          {visibleStyles.map((style, index) => (
            <Badge 
              key={index}
              variant="outline"
              className="px-3 py-1.5 text-sm cursor-pointer hover:bg-accent font-normal"
              onClick={() => handleStyleClick(style)}
            >
              {style}
            </Badge>
          ))}
          
          {stylesList.length > initialStylesCount && (
            <Button 
              variant="ghost" 
              size="sm" 
              onClick={toggleStyles}
              className="h-8 text-xs"
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
      </div>
    );
  };
  
  return (
    <div className="p-2">
      <div className="mb-1 text-xs text-muted-foreground">Try an example:</div>
      {renderExamples()}
      
      <div className="mb-1 text-xs text-muted-foreground">Add a style:</div>
      {renderStyles()}
    </div>
  );
};

export default PromptExamples;
