
import React, { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { 
  Dialog, 
  DialogContent, 
  DialogTitle 
} from '@/components/ui/dialog';

interface ImagePromptProps {
  prompt?: string;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ prompt }) => {
  const [showFullPrompt, setShowFullPrompt] = useState(false);
  
  if (!prompt) return null;
  
  const isLongPrompt = prompt.length > 70; // Threshold for showing the expand button
  const displayPrompt = isLongPrompt ? `${prompt.substring(0, 70)}...` : prompt;
  
  return (
    <>
      <div className="text-sm text-muted-foreground flex items-center w-full px-2 overflow-hidden">
        <p className="truncate">{displayPrompt}</p>
        {isLongPrompt && (
          <button 
            onClick={(e) => {
              e.stopPropagation();
              setShowFullPrompt(true);
            }}
            className="ml-1 flex-shrink-0 hover:bg-secondary/40 rounded-full p-1"
            aria-label="View full prompt"
          >
            <ExternalLink size={16} />
          </button>
        )}
      </div>
      
      {/* Full prompt dialog */}
      <Dialog open={showFullPrompt} onOpenChange={setShowFullPrompt}>
        <DialogContent>
          <DialogTitle>Prompt</DialogTitle>
          <div className="mt-2 whitespace-pre-wrap break-words max-h-[60vh] overflow-y-auto">
            {prompt}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default ImagePrompt;
