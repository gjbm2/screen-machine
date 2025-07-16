import React, { useEffect, useRef } from 'react';
import { Textarea } from '@/components/ui/textarea';
import { X } from 'lucide-react';
import { toast } from 'sonner';
import PromptExamples from './PromptExamples';
import ReferenceImagesSection from '@/components/image-display/ReferenceImagesSection';
import { useDroppable } from '@dnd-kit/core';
import { DROP_ZONES } from '@/dnd/dropZones';

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
  minHeight = "min-h-[48px]",
  multiline = true,
  maxLength,
  onSubmit,
  isFirstRun
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { setNodeRef, isOver } = useDroppable({
    id: DROP_ZONES.PROMPT,
    data: {
      type: 'prompt-area',
      accepts: ['image'],
      isPrompt: true
    }
  });

  const handleClearPrompt = () => {
    if (onClearPrompt) onClearPrompt();
    if (onClearAllImages) onClearAllImages();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter') {
      if (!multiline || e.ctrlKey || e.metaKey) {
        e.preventDefault();
        if (onSubmit && !isLoading && prompt.trim().length > 0) {
          onSubmit();
        }
      } else if (!multiline) {
        e.preventDefault();
      } else if (!e.shiftKey && onSubmit && !isLoading && prompt.trim().length > 0) {
        e.preventDefault();
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
    const combinedPrompt = prompt.trim() ? `${prompt.trim()}, ${stylePrompt}` : stylePrompt;
    const event = {
      target: { value: combinedPrompt },
      preventDefault: () => {},
      stopPropagation: () => {},
    } as React.ChangeEvent<HTMLTextAreaElement>;
    onPromptChange(event);
  };

  const autoResize = () => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = `${Math.min(el.scrollHeight, window.innerHeight * 0.5)}px`;
    }
  };

  useEffect(() => {
    autoResize();
  }, [prompt]);

  return (
    <div 
      ref={setNodeRef}
      className={`relative rounded-lg transition-all duration-200 ${
        isOver 
          ? 'ring-2 ring-primary ring-offset-2 bg-primary/5' 
          : ''
      }`}
    >
      {(prompt || (uploadedImages && uploadedImages.length > 0)) && (
        <button
          type="button"
          onClick={handleClearPrompt}
          className="absolute top-0 right-0 text-muted-foreground hover:text-foreground p-1 rounded-full transition-colors z-10"
          aria-label="Clear prompt and images"
        >
          <X className="h-4 w-4" />
        </button>
      )}

      {uploadedImages.length > 0 && (
        <div className="mb-3">
          <ReferenceImagesSection 
            images={uploadedImages} 
            onRemoveImage={onRemoveImage || onClearAllImages ? (index) => {
              if (onRemoveImage) onRemoveImage(index);
              else if (onClearAllImages) onClearAllImages();
            } : undefined} 
          />
        </div>
      )}

      <Textarea
        ref={textareaRef}
        placeholder={placeholder}
        className={`${minHeight} resize-none border-0 bg-transparent p-4 text-base placeholder:text-muted-foreground/50 focus-visible:ring-0 overflow-hidden`}
        value={prompt}
        onChange={(e) => {
          e.stopPropagation();
          onPromptChange(e);
          autoResize();
        }}
        onKeyDown={handleKeyDown}
        disabled={isLoading}
        rows={multiline ? 2 : 1}
        style={{ maxHeight: '50vh' }}
      />

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
