
import React, { useRef } from 'react';
import { toast } from 'sonner';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ImageUploaderProps {
  isLoading: boolean;
  onImageUpload: (file: File | null) => void;
  onWorkflowChange: (workflowId: string) => void;
}

const ImageUploader: React.FC<ImageUploaderProps> = ({
  isLoading,
  onImageUpload,
  onWorkflowChange,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Check if file is an image
    if (!file.type.startsWith('image/')) {
      toast.error('Please upload an image file');
      return;
    }

    // Check file size (max 5MB)
    if (file.size > 5 * 1024 * 1024) {
      toast.error('Image size should be less than 5MB');
      return;
    }

    onImageUpload(file);
    
    // If uploading an image, automatically switch to image-to-image workflow
    onWorkflowChange('image-to-image');
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
      />
      
      <Button 
        type="button" 
        variant="outline"
        onClick={triggerFileInput}
        className="text-sm flex items-center gap-2 flex-1"
        disabled={isLoading}
      >
        <Upload className="h-4 w-4" />
        Upload Image
      </Button>
    </>
  );
};

export default ImageUploader;
