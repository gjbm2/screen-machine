import React, { useRef, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Upload, Camera } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useIsMobile } from '@/hooks/use-mobile';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuPortal,
} from "@/components/ui/dropdown-menu";
import type { Workflow } from '@/components/prompt-form/types';

interface ImageUploaderProps {
  isLoading: boolean;
  onImageUpload: (files: File[]) => void;
  onWorkflowChange: (workflowId: string) => void;
  availableWorkflows: Workflow[];
  hideLabel?: boolean;
  selectedWorkflowId?: string;
}

function findNextImageWorkflow(workflows: Workflow[], currentWorkflowId?: string): string | null {
  const current = workflows.find(wf => wf.id === currentWorkflowId);
  if (current?.input?.includes('image')) return null; // already supports image

  for (const wf of workflows) {
    if (wf.input?.includes('image')) return wf.id;
  }
  return null;
}

const ImageUploader: React.FC<ImageUploaderProps> = ({
  isLoading,
  onImageUpload,
  onWorkflowChange,
  availableWorkflows,
  hideLabel = false,
  selectedWorkflowId
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    const handleImageSelected = (event: CustomEvent) => {
      if (event.detail && event.detail.files) {
        onImageUpload(event.detail.files);
        const nextWorkflow = findNextImageWorkflow(availableWorkflows, selectedWorkflowId);
        if (nextWorkflow) {
          onWorkflowChange(nextWorkflow);
          toast.info(`Switched to "${nextWorkflow}" workflow to support image input`);
        }
      }
    };

    document.addEventListener('image-selected', handleImageSelected as EventListener);
    return () => {
      document.removeEventListener('image-selected', handleImageSelected as EventListener);
    };
  }, [onImageUpload, onWorkflowChange, availableWorkflows, selectedWorkflowId]);

  const processFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const validFiles: File[] = [];
    Array.from(files).forEach(file => {
      if (!file.type.startsWith('image/')) {
        toast.error(`${file.name} is not an image file`);
        return;
      }
      if (file.size > 5 * 1024 * 1024) {
        toast.error(`${file.name} exceeds the 5MB size limit`);
        return;
      }
      validFiles.push(file);
    });

    if (validFiles.length > 0) {
      onImageUpload(validFiles);
      const nextWorkflow = findNextImageWorkflow(availableWorkflows, selectedWorkflowId);
      if (nextWorkflow) {
        onWorkflowChange(nextWorkflow);
        toast.info(`Switched to "${nextWorkflow}" workflow to support image input`);
      }
    }
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    processFiles(e.target.files);
    e.target.value = '';
  };

  const processURL = async (url: string) => {
    try {
      const isValidURL = /^(https?:\/\/)/i.test(url);
      if (!isValidURL) {
        toast.error("Invalid URL format");
        return;
      }

      const response = await fetch(url, { method: 'HEAD' });
      
      if (!response.ok) {
        toast.error("Could not access the URL");
        return;
      }
      
      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.startsWith('image/')) {
        toast.error("URL does not point to a valid image");
        return;
      }
      
      const imageResponse = await fetch(url);
      const blob = await imageResponse.blob();
      const fileName = url.split('/').pop() || 'image.jpg';
      const file = new File([blob], fileName, { type: blob.type });
      
      onImageUpload([file]);
      
      const nextWorkflow = findNextImageWorkflow(availableWorkflows, selectedWorkflowId);
      if (nextWorkflow) {
        onWorkflowChange(nextWorkflow);
        toast.info(`Switched to "${nextWorkflow}" workflow to support image input`);
      }
    } catch (error) {
      console.error('Error processing URL:', error);
      toast.error("Failed to process image URL");
    }
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isLoading) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isLoading) {
      setIsDragging(true);
      e.dataTransfer.dropEffect = 'copy';
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    if (isLoading) return;
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processFiles(e.dataTransfer.files);
      return;
    }
    
    const url = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text');
    if (url) {
      processURL(url);
    }
  };

  const triggerFileInput = () => fileInputRef.current?.click();
  const triggerCameraInput = () => cameraInputRef.current?.click();

  const dragButtonClass = `${isDragging ? 'bg-purple-100 border-purple-500' : 'border border-input'} transition-colors`;

  if (isMobile) {
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
        <input
          type="file"
          ref={cameraInputRef}
          className="hidden"
          accept="image/*"
          onChange={handleImageUpload}
          disabled={isLoading}
          capture="environment"
        />

        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="outline"
              className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 ${dragButtonClass} ${
                menuOpen
                  ? 'bg-purple-500/10 text-purple-700'
                  : 'hover:bg-purple-500/10 text-purple-700'
              }`}
              disabled={isLoading}
              ref={buttonRef}
              onDragEnter={handleDragEnter}
              onDragLeave={handleDragLeave}
              onDragOver={handleDragOver}
              onDrop={handleDrop}
            >
              <Upload className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuPortal>
            <DropdownMenuContent align="start" className="w-64 p-2">
              <div className="text-sm font-semibold mb-1 px-2">Upload options</div>
              <DropdownMenuItem onClick={triggerFileInput} className="px-4 py-1.5 text-sm">
                <Upload className="h-4 w-4 mr-2" />
                From Gallery
              </DropdownMenuItem>
              <DropdownMenuItem onClick={triggerCameraInput} className="px-4 py-1.5 text-sm">
                <Camera className="h-4 w-4 mr-2" />
                From Camera
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenuPortal>
        </DropdownMenu>
      </>
    );
  }

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
        className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 hover:bg-purple-500/10 text-purple-700 ${dragButtonClass}`}
        disabled={isLoading}
        ref={buttonRef}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <Upload className="h-4 w-4" />
        {!hideLabel && "Upload"}
      </Button>
    </>
  );
};

export default ImageUploader;
