
import React from 'react';
import { Card } from '@/components/ui/card';
import { Image, Loader2 } from 'lucide-react';

interface LoadingPlaceholderProps {
  prompt: string | null;
  imageNumber?: number;
  workflowName?: string;
  hasReferenceImages?: boolean;
  isCompact?: boolean; // Added this prop
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ 
  prompt, 
  imageNumber, 
  workflowName,
  hasReferenceImages,
  isCompact = false // Added with default value
}) => {
  // Determine what text to display
  let displayText = prompt;
  if (!displayText && (imageNumber !== undefined || workflowName)) {
    displayText = `${imageNumber !== undefined ? `Image ${imageNumber}` : ''} ${workflowName ? `- ${workflowName}` : ''}`.trim();
  }

  // Use simpler view for compact mode
  if (isCompact) {
    return (
      <div className="aspect-square rounded-md overflow-hidden bg-secondary/10 flex flex-col items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin mb-1 text-primary" />
        {displayText && (
          <p className="text-xs text-center text-muted-foreground px-2 max-w-full overflow-hidden truncate">
            {displayText}
          </p>
        )}
      </div>
    );
  }

  return (
    <Card className="overflow-hidden w-full rounded-b-lg rounded-t-none">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <Loader2 className="h-12 w-12 animate-spin mb-4 text-primary" />
        {displayText && (
          <p className="text-sm text-center text-muted-foreground px-4 max-w-full overflow-hidden flex items-center justify-center">
            {hasReferenceImages && (
              <span className="mr-1">
                <Image size={14} />
              </span>
            )}
            <span className="truncate inline-block max-w-xs">Generating: {displayText}</span>
          </p>
        )}
      </div>
    </Card>
  );
};

export default LoadingPlaceholder;
