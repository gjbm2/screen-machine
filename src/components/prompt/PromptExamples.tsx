
import React, { useState } from 'react';
import { toast } from 'sonner';
import examplePromptsData from '@/data/example-prompts.json';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface PromptExamplesProps {
  prompt: string;
  onExampleClick: (example: string) => void;
  onStyleClick: (style: string) => void;
}

const PromptExamples: React.FC<PromptExamplesProps> = ({
  prompt,
  onExampleClick,
  onStyleClick,
}) => {
  const [showMoreBasic, setShowMoreBasic] = useState(false);
  const [showMoreStyles, setShowMoreStyles] = useState(false);
  
  const handleBasicPromptClick = (example: string) => {
    onExampleClick(example);
  };

  const handleStyleClick = (style: string) => {
    // Remove the "+" prefix from the style for appending
    const styleText = style.startsWith('+') ? style.substring(1).trim() : style;
    
    // Check if the prompt already contains this style to avoid duplication
    if (prompt.includes(styleText)) {
      toast.info('This style is already applied to your prompt');
      return;
    }
    
    // Apply the style, even if the prompt is empty
    onStyleClick(`${prompt.trim()} ${styleText}`.trim());
  };

  const visiblePrompts = showMoreBasic ? examplePromptsData.basicPrompts : examplePromptsData.basicPrompts.slice(0, 3);
  const visibleStyles = showMoreStyles ? examplePromptsData.stylePrompts : examplePromptsData.stylePrompts.slice(0, 3);

  return (
    <div className="px-4 pb-3">
      <div className="flex flex-wrap gap-2 mb-3 items-center">
        <span className="text-xs text-muted-foreground whitespace-nowrap">Try an example:</span>
        <div className="flex-1 flex flex-wrap gap-2">
          {visiblePrompts.map((example, index) => (
            <button
              key={`basic-${index}`}
              type="button"
              className="text-xs bg-secondary/50 hover:bg-secondary px-2 py-1 rounded-full text-foreground/70 transition-colors text-[10px]"
              onClick={() => handleBasicPromptClick(example)}
            >
              {example.length > 30 ? `${example.slice(0, 30)}...` : example}
            </button>
          ))}
          
          {examplePromptsData.basicPrompts.length > 3 && (
            <Button
              variant="ghost"
              size="sm"
              className="text-[10px] h-6 px-2 rounded-full"
              onClick={() => setShowMoreBasic(!showMoreBasic)}
            >
              {showMoreBasic ? (
                <>Less <ChevronUp className="ml-1 h-3 w-3" /></>
              ) : (
                <>More <ChevronDown className="ml-1 h-3 w-3" /></>
              )}
            </Button>
          )}
        </div>
      </div>
      
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-xs text-muted-foreground whitespace-nowrap">Add a style:</span>
        <div className="flex-1 flex flex-wrap gap-2">
          {visibleStyles.map((style, index) => (
            <button
              key={`style-${index}`}
              type="button"
              className="text-[10px] bg-purple-500/20 hover:bg-purple-500/30 px-2 py-1 rounded-full text-purple-700 transition-colors"
              onClick={() => handleStyleClick(style)}
            >
              {style.length > 30 ? `${style.slice(0, 30)}...` : style}
            </button>
          ))}
          
          {examplePromptsData.stylePrompts.length > 3 && (
            <Button
              variant="ghost"
              size="sm"
              className="text-[10px] h-6 px-2 rounded-full"
              onClick={() => setShowMoreStyles(!showMoreStyles)}
            >
              {showMoreStyles ? (
                <>Less <ChevronUp className="ml-1 h-3 w-3" /></>
              ) : (
                <>More <ChevronDown className="ml-1 h-3 w-3" /></>
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};

export default PromptExamples;
