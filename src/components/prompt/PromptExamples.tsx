
import React from 'react';
import { toast } from 'sonner';
import examplePromptsData from '@/data/example-prompts.json';

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

  return (
    <div className="px-4 pb-3">
      <p className="text-xs text-muted-foreground mb-2">Try an example:</p>
      <div className="flex flex-wrap gap-2 mb-3">
        {examplePromptsData.basicPrompts.map((example, index) => (
          <button
            key={`basic-${index}`}
            type="button"
            className="text-xs bg-secondary/50 hover:bg-secondary px-2 py-1 rounded-full text-foreground/70 transition-colors"
            onClick={() => handleBasicPromptClick(example)}
          >
            {example.length > 30 ? `${example.slice(0, 30)}...` : example}
          </button>
        ))}
      </div>
      
      <p className="text-xs text-muted-foreground mb-2">Add a style:</p>
      <div className="flex flex-wrap gap-2">
        {examplePromptsData.stylePrompts.map((style, index) => (
          <button
            key={`style-${index}`}
            type="button"
            className="text-xs bg-purple-500/20 hover:bg-purple-500/30 px-2 py-1 rounded-full text-purple-700 transition-colors"
            onClick={() => handleStyleClick(style)}
          >
            {style.length > 30 ? `${style.slice(0, 30)}...` : style}
          </button>
        ))}
      </div>
    </div>
  );
};

export default PromptExamples;
