
import React from 'react';

interface ImagePromptProps {
  prompt?: string;
  expanded?: boolean;
  onToggleExpand?: () => void;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ 
  prompt,
  expanded = false,
  onToggleExpand
}) => {
  if (!prompt) return null;
  
  return (
    <div className="text-sm text-muted-foreground text-center mx-auto w-full px-4 overflow-hidden">
      <div className={`${expanded ? 'whitespace-normal break-words' : 'truncate'} w-full`}>
        {prompt}
      </div>
    </div>
  );
};

export default ImagePrompt;
