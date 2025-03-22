
import React from 'react';
import { Card } from '@/components/ui/card';
import { Image } from 'lucide-react';

interface LoadingPlaceholderProps {
  prompt: string | null;
  imageNumber?: number;
  workflowName?: string;
  hasReferenceImages?: boolean;
}

const LoadingPlaceholder: React.FC<LoadingPlaceholderProps> = ({ 
  prompt, 
  imageNumber, 
  workflowName,
  hasReferenceImages
}) => {
  // Determine what text to display
  let displayText = prompt;
  if (!displayText && (imageNumber !== undefined || workflowName)) {
    displayText = `${imageNumber !== undefined ? `Image ${imageNumber}` : ''} ${workflowName ? `- ${workflowName}` : ''}`.trim();
  }

  return (
    <Card className="overflow-hidden w-full rounded-b-lg rounded-t-none">
      <div className="aspect-square flex flex-col items-center justify-center bg-secondary/10">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent mb-4"></div>
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
