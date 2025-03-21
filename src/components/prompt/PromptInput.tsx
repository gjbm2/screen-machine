
import React from 'react';
import { Textarea } from '@/components/ui/textarea';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import { ReferenceImageData } from '@/types/workflows';

interface PromptInputProps {
  prompt: string;
  isLoading: boolean;
  uploadedImages?: string[];
  onPromptChange: (prompt: string) => void;
  onClearPrompt?: () => void;
  placeholder?: string;
  minHeight?: string;
  multiline?: boolean;
  maxLength?: number;
  onSubmit?: () => void;
}

const PromptInput: React.FC<PromptInputProps> = ({ 
  prompt, 
  isLoading,
  uploadedImages = [],
  onPromptChange,
  onClearPrompt,
  placeholder = "Describe the image you want to create...",
  minHeight = "min-h-[120px]",
  multiline = true,
  maxLength,
  onSubmit
}) => {
  const handleClearPrompt = () => {
    if (onClearPrompt) {
      onClearPrompt();
    } else {
      onPromptChange('');
      toast.info('Prompt cleared');
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    
    // Apply maxLength constraint if specified
    if (maxLength && value.length > maxLength) {
      onPromptChange(value.slice(0, maxLength));
      return;
    }
    
    onPromptChange(value);
  };

  // Handle Enter key if not multiline
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (!multiline && e.key === 'Enter') {
      e.preventDefault();
    }
    
    // Submit on Enter key if onSubmit handler is provided
    if (e.key === 'Enter' && !e.shiftKey && onSubmit) {
      e.preventDefault();
      onSubmit();
    }
  };

  return (
    <div className="relative">
      <Textarea
        placeholder={placeholder}
        className={`${minHeight} resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0`}
        value={prompt}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        disabled={isLoading}
        rows={multiline ? 3 : 1}
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
      
      {maxLength && (
        <div className="absolute bottom-1 right-3 text-xs text-muted-foreground">
          {prompt.length}/{maxLength}
        </div>
      )}
    </div>
  );
};

export default PromptInput;
