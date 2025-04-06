
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
    
    const avgExampleWidth = 155;
    const labelWidth = 30;
    const moreButtonWidth = 80;
    const availableWidth = width - 75;
    
    const examplesCapacity = Math.max(1, Math.floor((availableWidth - labelWidth - moreButtonWidth) / avgExampleWidth));
	
	
	if (width < 375) {
      setInitialExamplesCount(2);
    } else if (width < 500) {
      setInitialExamplesCount(Math.min(3, examplesCapacity));
    } else if (width < 640) {
      setInitialExamplesCount(Math.min(4, examplesCapacity));
    } else if (width < 768) {
      setInitialExamplesCount(Math.min(5, examplesCapacity));
    } else if (width < 1024) {
      setInitialExamplesCount(Math.min(6, examplesCapacity));
    } else {
      setInitialExamplesCount(showMore ? Math.min(8, examplesCapacity) : Math.min(6, examplesCapacity));
    }
	
    
    const avgStyleWidth = 110;
    const styleLabelWidth = 40;
    
    const stylesCapacity = Math.max(1, Math.floor((availableWidth - styleLabelWidth - moreButtonWidth) / avgStyleWidth));
    
    if (width < 375) {
      setInitialStylesCount(2);
    } else if (width < 500) {
      setInitialStylesCount(Math.min(3, stylesCapacity));
    } else if (width < 640) {
      setInitialStylesCount(Math.min(4, stylesCapacity));
    } else if (width < 768) {
      setInitialStylesCount(Math.min(5, stylesCapacity));
    } else if (width < 1024) {
      setInitialStylesCount(Math.min(6, stylesCapacity));
    } else {
      setInitialStylesCount(showMore ? Math.min(10, stylesCapacity) : Math.min(6, stylesCapacity));
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
    <div className="p-2 pt-4 space-y-1.5">
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
