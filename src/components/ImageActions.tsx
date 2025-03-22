
import React from 'react';
import { Button } from '@/components/ui/button';
import { 
  Download, 
  RefreshCw, 
  ImageIcon, 
  Trash2
} from 'lucide-react';
import { saveAs } from 'file-saver';

interface ImageActionsProps {
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  onDeleteImage?: () => void;
  generationInfo?: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
    title?: string;  // Add title to generationInfo
  };
  alwaysVisible?: boolean;
  showThumbnail?: boolean;
  small?: boolean;
  title?: string; // Direct title prop
}

const ImageActions: React.FC<ImageActionsProps> = ({
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  generationInfo,
  alwaysVisible = false,
  showThumbnail = false,
  small = false,
  title // Direct title prop
}) => {
  const handleDownload = () => {
    if (!imageUrl) return;
    
    // First check direct title prop, then generationInfo.title, then use timestamp
    const titleToUse = title || (generationInfo?.title) || null;
    const filename = titleToUse 
      ? `${titleToUse.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.png`
      : `image_${Date.now()}.png`;
    
    saveAs(imageUrl, filename);
  };

  return (
    <div className="flex gap-2">
      {/* Use as Input Button */}
      {onUseAsInput && (
        <Button
          variant="ghost"
          size={small ? 'icon' : 'default'}
          className={small ? 'h-8 w-8' : ''}
          onClick={onUseAsInput}
          title="Use as Input"
        >
          {small ? <ImageIcon className="h-4 w-4" /> : 'Use as Input'}
        </Button>
      )}

      {/* Create Again Button */}
      {onCreateAgain && (
        <Button
          variant="ghost"
          size={small ? 'icon' : 'default'}
          className={small ? 'h-8 w-8' : ''}
          onClick={onCreateAgain}
          title="Create Again"
        >
          {small ? <RefreshCw className="h-4 w-4" /> : 'Create Again'}
        </Button>
      )}

      {/* Download Button */}
      <Button
        variant="ghost"
        size={small ? 'icon' : 'default'}
        className={small ? 'h-8 w-8' : ''}
        onClick={handleDownload}
        title="Download Image"
      >
        {small ? <Download className="h-4 w-4" /> : 'Download'}
      </Button>

      {/* Delete Button */}
      {onDeleteImage && (
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={onDeleteImage}
          title="Delete Image"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
};

export default ImageActions;
