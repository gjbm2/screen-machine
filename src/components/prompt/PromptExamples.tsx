
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
  
  // Determine how many examples and styles to show based on screen width
  useEffect(() => {
    if (!width) return;
    
    // Calculate how many example prompts we can fit based on width
    // Assuming average example is ~18 characters at ~9px per character in 'text-sm'
    // Plus badge padding, margins, and some buffer space
    const avgExampleWidth = 180; // Approximate width in pixels for average example
    const labelWidth = 40; // Width for "Try:" label
    const moreButtonWidth = 70; // Width for "More" button
    const availableWidth = width - 40; // Subtract container padding
    
    // Calculate how many examples would fit in the available width
    const examplesCapacity = Math.max(1, Math.floor((availableWidth - labelWidth - moreButtonWidth) / avgExampleWidth));
    setInitialExamplesCount(Math.min(3, examplesCapacity)); // Cap at 3 examples max
    
    // Calculate style prompts similarly but with smaller average width
    const avgStyleWidth = 150; // Styles are usually shorter
    const styleLabelWidth = 50; // Width for "Style:" label
    
    const stylesCapacity = Math.max(1, Math.floor((availableWidth - styleLabelWidth - moreButtonWidth) / avgStyleWidth));
    
    // Set styles count based on screen size with fine-tuned values
    if (width < 375) {
      setInitialStylesCount(1); // Smallest screens show at least 1
    } else if (width < 500) {
      setInitialStylesCount(Math.min(2, stylesCapacity)); // Small mobile screens
    } else if (width < 640) {
      setInitialStylesCount(Math.min(3, stylesCapacity)); // Regular mobile screens
    } else if (width < 768) {
      setInitialStylesCount(Math.min(4, stylesCapacity)); // Larger mobile screens
    } else if (width < 1024) {
      setInitialStylesCount(Math.min(5, stylesCapacity)); // Tablet screens
    } else {
      setInitialStylesCount(showMore ? Math.min(8, stylesCapacity) : Math.min(6, stylesCapacity)); // Desktop screens
    }
  }, [width, showMore]);
  
  // Randomize examples and styles on mount and whenever they change
  useEffect(() => {
    // Randomize examples
    const examplesList = [...(examplePrompts.basicPrompts || [])];
    const shuffledExamples = examplesList.sort(() => Math.random() - 0.5);
    setRandomizedExamples(shuffledExamples);
    
    // Randomize styles
    const stylesList = [...(examplePrompts.stylePrompts || [])];
    const shuffledStyles = stylesList.sort(() => Math.random() - 0.5);
    setRandomizedStyles(shuffledStyles);
  }, []);
  
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
    if (randomizedExamples.length === 0) {
      return null;
    }
    
    // Show randomized examples up to the calculated initial count
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
    // Use randomized styles
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
