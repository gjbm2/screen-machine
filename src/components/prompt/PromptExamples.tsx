
import React, { useState, useEffect } from 'react';
import examplePrompts from '@/data/example-prompts.json';
import { useWindowSize } from '@/hooks/use-mobile';
import PromptExamplesList from './PromptExamplesList';
import StylePromptsList from './StylePromptsList';

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
  const [randomizedExamples, setRandomizedExamples] = useState<string[]>([]);
  const [randomizedStyles, setRandomizedStyles] = useState<{display: string, prompt: string}[]>([]);
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
  
  const handleStyleClick = (stylePrompt: {display: string, prompt: string}) => {
    onStyleClick(stylePrompt.prompt);
  };
  
  return (
    <div className="p-1 space-y-1.5">
      <PromptExamplesList 
        examples={randomizedExamples} 
        initialCount={initialExamplesCount} 
        onExampleClick={handleExampleClick}
      />
      
      <StylePromptsList 
        styles={randomizedStyles} 
        initialCount={initialStylesCount} 
        onStyleClick={handleStyleClick}
      />
    </div>
  );
};

export default PromptExamples;
