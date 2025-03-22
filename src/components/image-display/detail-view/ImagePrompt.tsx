
import React, { useState, useRef, useEffect } from 'react';
import { ExternalLink } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

interface ImagePromptProps {
  prompt?: string;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({ prompt }) => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isTruncated, setIsTruncated] = useState(false);
  const textRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (textRef.current) {
      setIsTruncated(
        textRef.current.scrollWidth > textRef.current.clientWidth
      );
    }
  }, [prompt]);

  if (!prompt) return null;
  
  return (
    <>
      <div className="flex items-center space-x-1 text-sm text-muted-foreground max-w-full overflow-hidden">
        <p 
          ref={textRef}
          className="truncate"
          title={isTruncated ? "Click to view full prompt" : undefined}
        >
          {prompt}
        </p>
        {isTruncated && (
          <Button 
            variant="ghost" 
            size="icon" 
            className="h-5 w-5 flex-shrink-0"
            onClick={() => setIsDialogOpen(true)}
          >
            <ExternalLink size={14} />
            <span className="sr-only">Show full prompt</span>
          </Button>
        )}
      </div>

      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="max-w-2xl max-h-[80vh]">
          <DialogTitle>Image Prompt</DialogTitle>
          <div className="overflow-auto max-h-[calc(80vh-100px)]">
            <p className="text-muted-foreground whitespace-pre-wrap">{prompt}</p>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default ImagePrompt;
