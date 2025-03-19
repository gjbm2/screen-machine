
import React, { useState, useEffect } from 'react';
import examplePrompts from '@/data/example-prompts.json';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface StylePrompt {
  display: string;
  prompt: string;
}

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
  const [randomExampleIndex, setRandomExampleIndex] = useState(0);
  const [selectedStyles, setSelectedStyles] = useState<string[]>([]);
  
  // Generate a random example on component mount
  useEffect(() => {
    const examplesList = examplePrompts.basicPrompts || [];
    const randomIndex = Math.floor(Math.random() * examplesList.length);
    setRandomExampleIndex(randomIndex);
  }, []);
  
  // Get the initial number of examples and styles to show based on screen size
  const initialExamplesCount = 1; // Just one example
  const initialStylesCount = showMore ? 3 : 2; // 2 styles on mobile, 3 on desktop
  
  const handleExampleClick = (example: string) => {
    onExampleClick(example);
  };
  
  const handleStyleClick = (stylePrompt: StylePrompt) => {
    // Store the selected style prompt text
    setSelectedStyles(prev => [...prev, stylePrompt.prompt]);
    
    // Combine current prompt with style
    const combinedPrompt = prompt ? `${prompt}, ${stylePrompt.prompt}` : stylePrompt.prompt;
    onStyleClick(combinedPrompt);
  };
  
  const toggleExamples = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setShowAllExamples(!showAllExamples);
  };
  
  const toggleStyles = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setShowAllStyles(!showAllStyles);
  };
  
  const renderExamples = () => {
    const examplesList = examplePrompts.basicPrompts || [];
    
    // If not showing all examples, just show the random one
    const visibleExamples = showAllExamples 
      ? examplesList 
      : [examplesList[randomExampleIndex]];
      
    return (
      <div className="flex flex-wrap gap-1.5 items-center">
        <span className="text-xs text-muted-foreground">Try:</span>
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
  
  const renderStyles = () => {
    const stylesList = examplePrompts.stylePrompts || [];
    
    const visibleStyles = showAllStyles
      ? stylesList
      : stylesList.slice(0, initialStylesCount);
      
    return (
      <div className="flex flex-wrap gap-1.5 items-center">
        <span className="text-xs text-muted-foreground">Style:</span>
        {visibleStyles.map((style, index) => (
          <Badge 
            key={index}
            variant="outline"
            className="px-3 py-1.5 text-sm cursor-pointer hover:bg-accent bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100 font-normal"
            onClick={() => handleStyleClick(style)}
          >
            {style.display}
          </Badge>
        ))}
        
        {stylesList.length > initialStylesCount && (
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={toggleStyles}
            className="h-8 text-xs"
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
  
  return (
    <div className="p-2 space-y-2">
      {renderExamples()}
      {renderStyles()}
    </div>
  );
};

export default PromptExamples;
