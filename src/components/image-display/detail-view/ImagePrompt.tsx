
import React, { useState } from 'react';
import { Maximize2 } from 'lucide-react';
import { 
  Dialog, 
  DialogContent, 
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';

interface ImagePromptProps {
  prompt?: string;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ prompt }) => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  
  if (!prompt) return null;
  
  const isLongPrompt = prompt.length > 70;
  const displayPrompt = isLongPrompt ? `${prompt.substring(0, 67)}...` : prompt;
  
  return (
    <>
      <div className="text-sm text-muted-foreground flex items-center justify-between max-w-full overflow-hidden">
        <p className="truncate">{displayPrompt}</p>
        {isLongPrompt && (
          <button 
            onClick={() => setIsDialogOpen(true)}
            className="flex-shrink-0 ml-2 p-1 rounded-full hover:bg-muted"
            aria-label="Expand prompt"
          >
            <Maximize2 size={14} />
          </button>
        )}
      </div>
      
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Prompt</DialogTitle>
          </DialogHeader>
          <div className="mt-4 text-sm whitespace-pre-wrap">
            {prompt}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default ImagePrompt;
