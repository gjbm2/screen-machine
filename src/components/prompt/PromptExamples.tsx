import React, { useState, useEffect } from 'react';
import examplePrompts from '@/data/example-prompts.json';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useWindowSize } from '@/hooks/use-mobile';

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
  const [selectedStyles, setSelectedStyles] = useState<string[]>([]);
  const [randomizedExamples, setRandomizedExamples] = useState<string[]>([]);
  const [randomizedStyles, setRandomizedStyles] = useState<StylePrompt[]>([]);
  const [initialExamplesCount, setInitialExamplesCount] = useState(1);
  const [initialStylesCount, setInitialStylesCount] = useState(2);
  const { width } = useWindowSize();
  
  useEffect(() => {
    if (!width) return;
    
    const avgExampleWidth = 180;
    const labelWidth = 40;
    const moreButtonWidth = 70;
    const availableWidth = width - 40;
    
    const examplesCapacity = Math.max(1, Math.floor((availableWidth - labelWidth - moreButtonWidth) / avgExampleWidth));
    setInitialExamplesCount(Math.min(3, examplesCapacity));
    
    const avgStyleWidth = 150;
    const styleLabelWidth = 50;
    
    const stylesCapacity = Math.max(1, Math.floor((availableWidth - styleLabelWidth - moreButtonWidth) / avgStyleWidth));
    
    if (width < 375) {
      setInitialStylesCount(1);
    } else if (width < 500) {
      setInitialStylesCount(Math.min(2, stylesCapacity));
    } else if (width < 640) {
      setInitialStylesCount(Math.min(3, stylesCapacity));
    } else if (width < 768) {
      setInitialStylesCount(Math.min(4, stylesCapacity));
    } else if (width < 1024) {
      setInitialStylesCount(Math.min(5, stylesCapacity));
    } else {
      setInitialStylesCount(showMore ? Math.min(8, stylesCapacity) : Math.min(6, stylesCapacity));
    }
  }, [width, showMore]);
  
  useEffect(() => {
    const examplesList = [...(examplePrompts.basicPrompts || [])];
    const shuffledExamples = examplesList.sort(() => Math.random() - 0.5);
    setRandomizedExamples(shuffledExamples);
    
    const stylesList = [...(examplePrompts.stylePrompts || [])];
    const shuffledStyles = stylesList.sort(() => Math.random() - 0.5);
    setRandomizedStyles(shuffledStyles);
  }, []);
  
  const handleExampleClick = (example: string) => {
    onExampleClick(example);
  };
  
  const handleStyleClick = (stylePrompt: StylePrompt) => {
    setSelectedStyles(prev => [...prev, stylePrompt.prompt]);
    onStyleClick(stylePrompt.prompt);
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
    if (randomizedExamples.length === 0) {
      return null;
    }
    
    const visibleExamples = showAllExamples 
      ? randomizedExamples 
      : randomizedExamples.slice(0, initialExamplesCount);
      
    return (
      <div className="flex flex-wrap gap-1 items-center">
        <span className="text-xs text-muted-foreground">Try:</span>
        {visibleExamples.map((example, index) => (
          <Badge 
            key={index}
            variant="secondary"
            className="px-2 py-1 text-sm cursor-pointer hover:bg-secondary/80 font-normal prompt-badge"
            onClick={() => handleExampleClick(example)}
          >
            {example}
          </Badge>
        ))}
        
        {randomizedExamples.length > initialExamplesCount && (
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
  
  const renderStyles = () => {
    const visibleStyles = showAllStyles
      ? randomizedStyles
      : randomizedStyles.slice(0, initialStylesCount);
      
    return (
      <div className="flex flex-wrap gap-1 items-center">
        <span className="text-xs text-muted-foreground">Style:</span>
        {visibleStyles.map((style, index) => (
          <Badge 
            key={index}
            variant="outline"
            className="px-2 py-1 text-sm cursor-pointer hover:bg-purple-100 bg-purple-50 text-purple-700 border-purple-200 font-normal prompt-badge"
            onClick={() => handleStyleClick(style)}
          >
            {style.display}
          </Badge>
        ))}
        
        {randomizedStyles.length > initialStylesCount && (
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={toggleStyles}
            className="h-7 text-xs px-1.5"
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
    <div className="p-1 space-y-1.5">
      {renderExamples()}
      {renderStyles()}
    </div>
  );
};

export default PromptExamples;
