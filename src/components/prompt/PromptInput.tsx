
import React from 'react';
import { Textarea } from '@/components/ui/textarea';
import { X, Image } from 'lucide-react';
import { toast } from 'sonner';
import { Dialog, DialogContent, DialogClose } from '@/components/ui/dialog';

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
      
      {/* Upload image indicator */}
      {uploadedImages.length > 0 && (
        <Dialog>
          <DialogContent className="sm:max-w-md">
            <div className="space-y-3">
              <h3 className="text-lg font-medium">Uploaded Reference Image</h3>
              <div className="aspect-square bg-secondary/20 rounded-md overflow-hidden">
                <img 
                  src={uploadedImages[0]} 
                  alt="Uploaded reference" 
                  className="w-full h-full object-contain"
                />
              </div>
            </div>
            <DialogClose className="absolute top-4 right-4" />
          </DialogContent>
          
          <button
            type="button"
            className="absolute bottom-3 left-3 bg-primary/10 hover:bg-primary/20 text-primary rounded-full p-1.5 transition-colors"
            aria-label="View uploaded image"
          >
            <Image className="h-4 w-4" />
          </button>
        </Dialog>
      )}
      
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
