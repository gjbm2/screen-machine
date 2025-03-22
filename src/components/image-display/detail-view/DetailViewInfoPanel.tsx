
import React from 'react';
import { Button } from '@/components/ui/button';
import { Copy, Download, Trash, Repeat, Upload, Info, ExternalLink } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import ImagePrompt from './ImagePrompt';
import { useIsMobile } from '@/hooks/use-mobile';

interface DetailViewInfoPanelProps {
  activeImage: any;
  dimensions: { width: number; height: number };
  referenceImageUrl?: string;
  showReferenceImage: boolean;
  setShowReferenceImage: (show: boolean) => void;
  handleCreateAgain: () => void;
  handleUseAsInput?: () => void;
  handleDeleteImage: () => void;
  onOpenInNewTab: (e: React.MouseEvent) => void;
  hidePrompt?: boolean;
  onInfoClick?: () => void;
  onClose?: () => void;
}

const DetailViewInfoPanel: React.FC<DetailViewInfoPanelProps> = ({
  activeImage,
  dimensions,
  referenceImageUrl,
  showReferenceImage,
  setShowReferenceImage,
  handleCreateAgain,
  handleUseAsInput,
  handleDeleteImage,
  onOpenInNewTab,
  hidePrompt = false,
  onInfoClick,
  onClose
}) => {
  const isMobile = useIsMobile();
  
  // Debug log the active image properties
  console.log("DetailViewInfoPanel - activeImage:", activeImage);
  if (referenceImageUrl) {
    console.log("DetailViewInfoPanel has referenceImageUrl:", referenceImageUrl);
  } else {
    console.log("DetailViewInfoPanel has no referenceImageUrl");
  }
  
  // We'll show text labels on desktop but icons only on mobile
  const buttonVariant = "outline";
  const buttonSize = isMobile ? "sm" : "default";
  
  return (
    <div className="flex-shrink-0 border-t">
      {/* Prompt (optional) */}
      {!hidePrompt && activeImage?.prompt && (
        <div className="px-4 py-2 border-b">
          <ImagePrompt 
            prompt={activeImage.prompt} 
            hasReferenceImages={Boolean(referenceImageUrl)}
            onReferenceImageClick={() => setShowReferenceImage(true)}
            onInfoClick={onInfoClick}
          />
        </div>
      )}
      
      {/* Action buttons */}
      <div className="p-2 bg-gray-50 flex flex-wrap items-center gap-2 justify-center sm:justify-start">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              onClick={handleCreateAgain}
              variant={buttonVariant}
              size={buttonSize}
              className="whitespace-nowrap"
            >
              <Repeat className="h-4 w-4 mr-1 sm:mr-2" />
              {!isMobile && "Go again"}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Generate another image like this one</p>
          </TooltipContent>
        </Tooltip>
        
        {handleUseAsInput && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={handleUseAsInput}
                variant={buttonVariant}
                size={buttonSize}
                className="whitespace-nowrap"
              >
                <Upload className="h-4 w-4 mr-1 sm:mr-2" />
                {!isMobile ? "Use as input" : "Input"}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Use this image as input for a new generation</p>
            </TooltipContent>
          </Tooltip>
        )}
        
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              onClick={onOpenInNewTab}
              variant={buttonVariant}
              size={buttonSize}
              className="whitespace-nowrap"
            >
              <ExternalLink className="h-4 w-4 mr-1 sm:mr-2" />
              {!isMobile && "Open"}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Open image in new tab</p>
          </TooltipContent>
        </Tooltip>
        
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              onClick={handleDeleteImage}
              variant="outline"
              size={buttonSize}
              className="whitespace-nowrap text-red-600 hover:text-red-700 hover:bg-red-50"
            >
              <Trash className="h-4 w-4 mr-1 sm:mr-2" />
              {!isMobile && "Delete"}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Delete this image</p>
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
};

export default DetailViewInfoPanel;
