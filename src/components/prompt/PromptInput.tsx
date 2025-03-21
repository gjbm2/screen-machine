import React from 'react';
import { Textarea } from '@/components/ui/textarea';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import { ReferenceImageData } from '@/types/workflows';
import PromptExamples from './PromptExamples';
import ReferenceImagesSection from '@/components/image-display/ReferenceImagesSection';

interface PromptInputProps {
  prompt: string;
  isLoading: boolean;
  uploadedImages?: string[];
  onPromptChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onClearPrompt?: () => void;
  onClearAllImages?: () => void;
  onRemoveImage?: (index: number) => void;
  placeholder?: string;
  minHeight?: string;
  multiline?: boolean;
  maxLength?: number;
  onSubmit?: () => void;
  isFirstRun?: boolean;
}

const PromptInput: React.FC<PromptInputProps> = ({ 
  prompt, 
  isLoading,
  uploadedImages = [],
  onPromptChange,
  onClearPrompt,
  onClearAllImages,
  onRemoveImage,
  placeholder = "Describe the image you want to create...",
  minHeight = "min-h-[120px]",
  multiline = true,
  maxLength,
  onSubmit,
  isFirstRun
}) => {
  const handleClearPrompt = () => {
    if (onClearPrompt) {
      onClearPrompt();
    }
    
    // Also clear images if there's a handler for it
    if (onClearAllImages) {
      onClearAllImages();
    }
  };

  // Handle Enter key
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      // If not multiline mode or if Ctrl/Cmd+Enter is pressed
      if (!multiline || e.ctrlKey || e.metaKey) {
        e.preventDefault(); // Prevent newline
        if (onSubmit && !isLoading && prompt.trim().length > 0) {
          onSubmit();
        }
      }
      // If multiline is false, always prevent default to avoid newlines
      else if (!multiline) {
        e.preventDefault();
      }
      // If just Enter is pressed in multiline mode with Shift key, allow newline
      else if (e.shiftKey) {
        // Allow default behavior (new line)
        return;
      }
      // If just plain Enter is pressed in multiline mode, submit the form
      else if (onSubmit && !isLoading && prompt.trim().length > 0) {
        e.preventDefault(); // Prevent newline
        onSubmit();
      }
    }
  };

  const handleExampleClick = (example: string) => {
    const event = {
      target: { value: example },
      preventDefault: () => {},
      stopPropagation: () => {},
    } as React.ChangeEvent<HTMLTextAreaElement>;
    
    onPromptChange(event);
  };

  const handleStyleClick = (stylePrompt: string) => {
    // If there's already text, append the style with a comma separator
    const combinedPrompt = prompt.trim() ? `${prompt.trim()}, ${stylePrompt}` : stylePrompt;
    
    const event = {
      target: { value: combinedPrompt },
      preventDefault: () => {},
      stopPropagation: () => {},
    } as React.ChangeEvent<HTMLTextAreaElement>;
    
    onPromptChange(event);
  };

  return (
    <div className="relative">
      {/* Reference Images Section - Only show at the top above the text input */}
      {uploadedImages && uploadedImages.length > 0 && (
        <div className="mb-3">
          <ReferenceImagesSection 
            images={uploadedImages} 
            onRemoveImage={onRemoveImage || onClearAllImages ? (index) => {
              // If we have a specific handler for single image removal, use it
              if (onRemoveImage) {
                onRemoveImage(index);
              } 
              // Otherwise fallback to clearing all images
              else if (onClearAllImages) {
                onClearAllImages();
              }
            } : undefined} 
          />
        </div>
      )}
      
      <Textarea
        placeholder={placeholder}
        className={`${minHeight} resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0`}
        value={prompt}
        onChange={onPromptChange}
        onKeyDown={handleKeyDown}
        disabled={isLoading}
        rows={multiline ? 3 : 1}
      />
      
      {(prompt || (uploadedImages && uploadedImages.length > 0)) && (
        <button
          type="button"
          onClick={handleClearPrompt}
          className="absolute top-3 right-3 text-muted-foreground hover:text-foreground p-1 rounded-full transition-colors"
          aria-label="Clear prompt and images"
        >
          <X className="h-4 w-4" />
        </button>
      )}
      
      {maxLength && (
        <div className="absolute bottom-1 right-3 text-xs text-muted-foreground">
          {prompt.length}/{maxLength}
        </div>
      )}

      <PromptExamples 
        prompt={prompt}
        onExampleClick={handleExampleClick}
        onStyleClick={handleStyleClick}
        showMore={!isLoading}
      />
    </div>
  );
};

export default PromptInput;
