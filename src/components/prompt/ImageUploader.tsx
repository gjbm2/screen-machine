
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
  const isMobile = useIsMobile();
  const [menuOpen, setMenuOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const handleImageSelected = (event: CustomEvent) => {
      if (event.detail && event.detail.files) {
        onImageUpload(event.detail.files);
        const nextWorkflow = findNextImageWorkflow(availableWorkflows, selectedWorkflowId);
        if (nextWorkflow) {
          onWorkflowChange(nextWorkflow);
          toast.info(`Switched to "${nextWorkflow}" workflow`);
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
        toast.info(`Switched to "${nextWorkflow}" workflow`);
      }
    }
  };

  const handleImageUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    processFiles(e.target.files);
    e.target.value = '';
  };

  const triggerFileInput = () => fileInputRef.current?.click();
  const triggerCameraInput = () => cameraInputRef.current?.click();

  const processURLS = async (text: string) => {
    try {
      // Attempt to extract URLs
      const urlRegex = /(https?:\/\/[^\s]+)/g;
      const urls = text.match(urlRegex);
      
      if (!urls || urls.length === 0) {
        toast.error('No valid URLs found in the dropped text');
        return;
      }
      
      const validFiles: File[] = [];
      
      for (const url of urls) {
        try {
          const response = await fetch(url);
          if (!response.ok) {
            toast.error(`Failed to fetch image from ${url}`);
            continue;
          }
          
          const contentType = response.headers.get('content-type');
          if (!contentType || !contentType.startsWith('image/')) {
            toast.error(`${url} is not an image`);
            continue;
          }
          
          const blob = await response.blob();
          const fileName = url.split('/').pop() || 'image.jpg';
          const file = new File([blob], fileName, { type: contentType });
          
          if (file.size > 5 * 1024 * 1024) {
            toast.error(`${fileName} exceeds the 5MB size limit`);
            continue;
          }
          
          validFiles.push(file);
        } catch (error) {
          console.error('Error fetching URL:', error);
          toast.error(`Failed to process ${url}`);
        }
      }
      
      if (validFiles.length > 0) {
        onImageUpload(validFiles);
        const nextWorkflow = findNextImageWorkflow(availableWorkflows, selectedWorkflowId);
        if (nextWorkflow) {
          onWorkflowChange(nextWorkflow);
          toast.info(`Switched to "${nextWorkflow}" workflow`);
        }
      }
    } catch (error) {
      console.error('Error processing URLs:', error);
      toast.error('Failed to process dropped text');
    }
  };

  // Drag and drop handlers
  const handleDragOver = (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = async (e: React.DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setIsDragging(false);
    
    if (e.dataTransfer.files.length > 0) {
      // Handle dropped files
      processFiles(e.dataTransfer.files);
    } else if (e.dataTransfer.items) {
      // Try to handle dropped text (potential URLs)
      for (let i = 0; i < e.dataTransfer.items.length; i++) {
        if (e.dataTransfer.items[i].kind === 'string') {
          e.dataTransfer.items[i].getAsString((text) => {
            processURLS(text);
          });
          break;
        }
      }
    }
  };

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
              className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 border border-input ${
                menuOpen
                  ? 'bg-purple-500/10 text-purple-700'
                  : 'hover:bg-purple-500/10 text-purple-700'
              } ${isDragging ? 'bg-purple-500/20 border-purple-500' : ''}`}
              disabled={isLoading}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              ref={buttonRef}
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
        className={`h-[36px] rounded-md flex-shrink-0 text-sm flex items-center gap-2 px-3 hover:bg-purple-500/10 text-purple-700 border border-input ${
          isDragging ? 'bg-purple-500/20 border-purple-500' : ''
        }`}
        disabled={isLoading}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        ref={buttonRef}
      >
        <Upload className="h-4 w-4" />
        {!hideLabel && "Upload"}
      </Button>
    </>
  );
};

export default ImageUploader;
