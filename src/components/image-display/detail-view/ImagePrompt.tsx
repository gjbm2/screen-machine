
import React from 'react';

interface ImagePromptProps {
  prompt?: string;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ prompt }) => {
  if (!prompt) return null;
  
  return (
    <div className="text-sm text-muted-foreground text-center max-w-lg mx-auto">
      <p>{prompt}</p>
    </div>
  );
};

export default ImagePrompt;
