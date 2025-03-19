
import React from 'react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Download, Share2 } from 'lucide-react';
import { toast } from 'sonner';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
}

const ImageDisplay = ({ imageUrl, prompt, isLoading }: ImageDisplayProps) => {
  // Mock function for downloading the image
  const handleDownload = () => {
    if (!imageUrl) return;
    
    // In a real app, you would implement the actual download logic
    window.open(imageUrl, '_blank');
  };
  
  // Mock function for sharing the image
  const handleShare = () => {
    if (!imageUrl) return;
    
    // In a real app, you would implement sharing functionality
    navigator.clipboard.writeText(imageUrl);
    toast.success('Image URL copied to clipboard');
  };

  if (isLoading) {
    return (
      <div className="mt-8 min-h-[400px] rounded-xl bg-secondary/30 backdrop-blur-sm flex items-center justify-center animate-fade-in">
        <div className="flex flex-col items-center justify-center">
          <div className="relative w-12 h-12">
            <span className="absolute inset-0 border-2 border-transparent border-t-primary rounded-full animate-spin"></span>
            <span className="absolute inset-0 border-2 border-primary/20 rounded-full opacity-50"></span>
          </div>
          <p className="mt-4 text-sm text-foreground/70">
            Creating your imagination...
          </p>
        </div>
      </div>
    );
  }

  if (!imageUrl) {
    return null;
  }

  return (
    <div className="mt-8 animate-fade-in">
      <Card className="overflow-hidden border border-border/30 image-container">
        <div className="aspect-square sm:aspect-video md:aspect-[4/3] relative overflow-hidden rounded-t-lg">
          <img 
            src={imageUrl} 
            alt={prompt || 'Generated image'} 
            className="h-full w-full object-cover animate-blur-in"
          />
        </div>
        <div className="p-4 flex justify-between items-center">
          <p className="text-sm text-muted-foreground line-clamp-1">{prompt}</p>
          <div className="flex gap-2">
            <Button 
              size="sm" 
              variant="outline" 
              className="rounded-full h-8 w-8 p-0" 
              onClick={handleDownload}
            >
              <Download className="h-4 w-4" />
              <span className="sr-only">Download</span>
            </Button>
            <Button 
              size="sm" 
              variant="outline" 
              className="rounded-full h-8 w-8 p-0" 
              onClick={handleShare}
            >
              <Share2 className="h-4 w-4" />
              <span className="sr-only">Share</span>
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default ImageDisplay;
