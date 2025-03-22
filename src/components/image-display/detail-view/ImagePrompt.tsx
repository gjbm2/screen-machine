
import React from 'react';

interface ImagePromptProps {
  prompt?: string;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ prompt }) => {
  if (!prompt) return null;
  
  return (
    <div className="text-sm text-muted-foreground text-center mx-auto max-w-full px-4 overflow-hidden text-ellipsis">
      <p className="truncate">{prompt}</p>
    </div>
  );
};

export default ImagePrompt;
