
import React, { useState, useEffect } from 'react';
import examplePrompts from '@/data/example-prompts.json';
import { useWindowSize } from '@/hooks/use-mobile';
import PromptExamplesList from './PromptExamplesList';
import StylePromptsList from './StylePromptsList';

/** What the prompt is for, so the suggestions fit it: a picture from words, a change to an attached picture, or a
 *  video (owner, 19 Nov: "the sorts of text they might want to append to the prompt is context-dependent"). */
export type PromptContext = 'text' | 'image' | 'video';

interface PromptExamplesProps {
  prompt: string;
  context?: PromptContext;
  onExampleClick: (example: string) => void;
  onStyleClick: (prompt: string) => void;
  showMore?: boolean;
}

const PromptExamples: React.FC<PromptExamplesProps> = ({ 
  prompt, 
  onExampleClick, 
  onStyleClick,
  showMore = false,
  context = 'text',
}) => {
  const contextRows = (examplePrompts as { contextPrompts?: Record<string, { label: string; prompts: { display: string; prompt: string }[] }[]> })
    .contextPrompts?.[context] || [];
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
      {/* a video or a change to an attached picture: suggestions that add to the prompt, not whole new ideas */}
      {context === 'text' && (
        <PromptExamplesList 
          examples={randomizedExamples} 
          initialCount={initialExamplesCount} 
          onExampleClick={handleExampleClick}
        />
      )}

      {contextRows.map((row) => (
        <StylePromptsList
          key={row.label}
          label={row.label}
          styles={row.prompts}
          initialCount={initialStylesCount}
          onStyleClick={handleStyleClick}
        />
      ))}

      {context !== 'video' && (
        <StylePromptsList 
          styles={randomizedStyles} 
          initialCount={initialStylesCount} 
          onStyleClick={handleStyleClick}
        />
      )}
    </div>
  );
};

export default PromptExamples;
