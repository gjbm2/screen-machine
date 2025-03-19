
import React, { useRef } from 'react';
import { toast } from 'sonner';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ImageUploaderProps {
  isLoading: boolean;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflowId: string) => void;
}

const ImageUploader: React.FC<ImageUploaderProps> = ({
  isLoading,
  onImageUpload,
  onWorkflowChange,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const validFiles: File[] = [];
    
    // Validate each file
    Array.from(files).forEach(file => {
      // Check if file is an image
      if (!file.type.startsWith('image/')) {
        toast.error(`${file.name} is not an image file`);
        return;
      }

      // Check file size (max 5MB)
      if (file.size > 5 * 1024 * 1024) {
        toast.error(`${file.name} exceeds the 5MB size limit`);
        return;
      }

      validFiles.push(file);
    });

    if (validFiles.length > 0) {
      onImageUpload(validFiles);
      
      // If uploading images, automatically switch to image-to-image workflow
      onWorkflowChange('image-to-image');
    }
  };

  const triggerFileInput = () => {
    // Reset the file input value before opening the file dialog
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
    fileInputRef.current?.click();
  };

  return (
    <>
      <input
        type="file"
        ref={fileInputRef}
        className="hidden"
        accept="image/*"
        onChange={handleImageUpload}
        disabled={isLoading}
        multiple
      />
      
      <Button 
        type="button" 
        variant="outline"
        onClick={triggerFileInput}
        className="rounded-full flex-shrink-0 text-sm flex items-center gap-2 px-4 h-[48px]"
        disabled={isLoading}
      >
        <Upload className="h-4 w-4" />
        Upload Images
      </Button>
    </>
  );
};

export default ImageUploader;
