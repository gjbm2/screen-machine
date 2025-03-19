
import React, { useState } from 'react';
import { Textarea } from '@/components/ui/textarea';
import { X } from 'lucide-react';
import { toast } from 'sonner';

interface PromptInputProps {
  prompt: string;
  isLoading: boolean;
  uploadedImages?: string[];
  onPromptChange: (prompt: string) => void;
}

const PromptInput: React.FC<PromptInputProps> = ({ 
  prompt, 
  isLoading, 
  uploadedImages = [],
  onPromptChange 
}) => {
  const handleClearPrompt = () => {
    onPromptChange('');
    toast.info('Prompt cleared');
  };

  return (
    <div className="relative">
      <Textarea
        placeholder="Describe the image you want to create..."
        className="min-h-[120px] resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0"
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        disabled={isLoading}
      />
      
      {prompt && (
        <button
          type="button"
          onClick={handleClearPrompt}
          className="absolute top-3 right-3 text-muted-foreground hover:text-foreground p-1 rounded-full transition-colors"
          aria-label="Clear prompt"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
};

export default PromptInput;
